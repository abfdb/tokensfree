"""Runtime assembly.

Turns configuration plus adapter instances into the Services the edge core needs.
Opening a socket and serving HTTP stay outside this module.
"""
import hashlib
import json
import pathlib
import time
from dataclasses import dataclass

from providers import ProviderSpec
from tokensfree.adapters.base import Admission, _ADMISSION_TOKEN
from tokensfree.adapters.loopback import LoopbackAdapter
from tokensfree.adapters.openai_compat import OpenAICompatAdapter
from tokensfree.adapters.transport import LoopbackStreamingAdapter
from tokensfree.adapters.mock import MockChatAdapter
from tokensfree.config import AppConfig, load_config
from tokensfree.edge.app_core import Services
from tokensfree import profiles as provider_profiles
from tokensfree.router.admission import AdmissionDenied, admit
from tokensfree.router.health import CircuitBreaker
from tokensfree.router.scheduling import RoundRobin
from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import AccountsRepo, EventsRepo, IdempotencyRepo, LedgerRepo, ProviderHealthRepo

# Fixture providers have no free-tier claim to verify; their evidence is only a
# safety assertion, so it must outlive a long-running demo server.
DEFAULT_FIXTURE_TTL_S = 10 ** 9

REAL_ENV_KEYS = (
    "TOKENSFREE_REAL_PROVIDER",
    "TOKENSFREE_REAL_BASE_URL",
    "TOKENSFREE_REAL_API_KEY",
    "TOKENSFREE_REAL_MODEL",
    "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT",
)
MAX_REAL_KEYS = 8
EVIDENCE_SOURCE_VARS = (
    "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT",
    "TOKENSFREE_REAL_EVIDENCE_SNAPSHOT",
)
REAL_DEFAULT_CAPABILITIES = frozenset({"text", "stream", "tools"})


@dataclass(frozen=True)
class Storage:
    """SQLite-backed idempotency, ledger and events for the runnable server."""

    connection: object
    idempotency: object
    ledger: object
    events: object
    health: object
    accounts: object

    @classmethod
    def open(cls, path=":memory:"):
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if path != ":memory:":
            expanded = pathlib.Path(path).expanduser()
            parent = expanded.parent
            if str(parent):
                parent.mkdir(parents=True, exist_ok=True)
            path = str(expanded)
        connection = connect(path)
        migrate(connection)
        return cls(
            connection=connection,
            idempotency=IdempotencyRepo(connection),
            ledger=LedgerRepo(connection),
            events=EventsRepo(connection),
            health=ProviderHealthRepo(connection),
            accounts=AccountsRepo(connection),
        )

    def close(self):
        self.connection.close()


@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    services: Services
    storage: object = None


def build_services(*, config, specs, adapters, authorized, clock=time.time, storage=None, breakers=None, rotation=None, account_ids=None, accounts=None, gate=None):
    specs = tuple(specs)
    names = {spec.name for spec in specs}
    if set(adapters) - names:
        raise ValueError("adapter registered without a provider spec")
    if not frozenset(authorized) <= names:
        raise ValueError("authorized provider has no spec")
    if not callable(clock):
        raise TypeError("clock must be callable")
    health = getattr(storage, "health", None)
    if breakers is None:
        breakers = {}
        for spec in specs:
            snapshot = health.get(spec.name) if health is not None else None
            breaker = (
                CircuitBreaker.from_snapshot(snapshot)
                if snapshot is not None
                else CircuitBreaker()
            )
            if health is not None:
                breaker.watch(lambda changed, name=spec.name: health.save(name, changed.snapshot()))
            breakers[spec.name] = breaker
    else:
        breakers = dict(breakers)
        unknown = set(breakers) - names
        if unknown:
            raise ValueError("breaker registered without a provider spec: " + ", ".join(sorted(unknown)))

    def default_gate(spec):
        admission = admit(spec, config, frozenset(authorized), clock())
        if account_ids and accounts is not None:
            account_id = account_ids.get(spec.name)
            if account_id is not None:
                row = accounts.get(account_id)
                if row is not None:
                    if row["status"] != "active":
                        raise AdmissionDenied("account_" + str(row["status"]))
                    cooldown_until = row["cooldown_until"]
                    if cooldown_until is not None and cooldown_until > clock():
                        raise AdmissionDenied("account_cooldown")
        return admission

    if gate is None:
        gate = default_gate
    elif not callable(gate):
        raise TypeError("gate must be callable")

    return Services(
        specs=specs,
        adapters=dict(adapters),
        admit=gate,
        authorized=frozenset(authorized),
        idempotency=getattr(storage, "idempotency", None),
        ledger=getattr(storage, "ledger", None),
        events=getattr(storage, "events", None),
        breakers=breakers,
        rotation=rotation,
        account_ids=account_ids,
        accounts=getattr(storage, "accounts", None),
    )


def build_loopback_runtime(port, *, config=None, clock=time.time, ttl_s=DEFAULT_FIXTURE_TTL_S, storage=None):
    config = load_config({}) if config is None else config
    spec = ProviderSpec("loopback", "fixture-model", "mock_zero_cost", clock() + ttl_s)
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=(spec,),
            adapters={"loopback": LoopbackAdapter(port)},
            authorized=frozenset({"loopback"}),
            clock=clock,
            storage=storage,
        ),
        storage=storage,
    )


def build_mock_runtime(*, config=None, clock=time.time, ttl_s=DEFAULT_FIXTURE_TTL_S, storage=None):
    config = load_config({}) if config is None else config
    specs = (
        ProviderSpec("mock-alpha", "mock-a", "mock_zero_cost", clock() + ttl_s),
        ProviderSpec("mock-beta", "mock-b", "mock_zero_cost", clock() + ttl_s),
    )
    adapters = {
        "mock-alpha": MockChatAdapter("mock-alpha", content="mock-alpha response"),
        "mock-beta": MockChatAdapter("mock-beta", content="mock-beta response"),
    }
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=specs,
            adapters=adapters,
            authorized=frozenset({"mock-alpha", "mock-beta"}),
            clock=clock,
            storage=storage,
        ),
        storage=storage,
    )


def build_loopback_streaming_runtime(port, *, config=None, clock=time.time, ttl_s=DEFAULT_FIXTURE_TTL_S, timeout=5.0, tools=False, storage=None):
    """Runtime whose single provider speaks SSE to a local fixture upstream."""
    config = load_config({}) if config is None else config
    capabilities = frozenset({"text", "stream"} | ({"tools"} if tools else set()))
    spec = ProviderSpec("loopback", "fixture-model", "mock_zero_cost", clock() + ttl_s, capabilities)
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=(spec,),
            adapters={"loopback": LoopbackStreamingAdapter(port, timeout=timeout)},
            authorized=frozenset({"loopback"}),
            clock=clock,
            storage=storage,
        ),
        storage=storage,
    )


KEY_SOURCE_VARS = (
    "TOKENSFREE_REAL_API_KEY",
    "TOKENSFREE_REAL_API_KEYS",
    "TOKENSFREE_REAL_API_KEY_FILE",
    "TOKENSFREE_REAL_API_KEYS_FILE",
    "TOKENSFREE_REAL_API_KEY_STORE",
)


def _keys_from_file(path):
    try:
        raw = pathlib.Path(str(path)).read_text(encoding="utf-8")
    except OSError:
        raise ValueError("cannot read API key file") from None
    return [line.strip() for line in raw.replace(",", "\n").splitlines() if line.strip()]


def real_keys_from_env(env):
    """One key or up to MAX_REAL_KEYS, inline or from a file (exactly one source)."""
    provided = [name for name in KEY_SOURCE_VARS if str(env.get(name, "")).strip()]
    if len(provided) > 1:
        raise ValueError("set only one of " + " / ".join(KEY_SOURCE_VARS))
    if not provided:
        raise ValueError("missing real-provider environment: TOKENSFREE_REAL_API_KEY")
    source = provided[0]
    if source == "TOKENSFREE_REAL_API_KEY":
        keys = [str(env[source]).strip()]
    elif source == "TOKENSFREE_REAL_API_KEYS":
        keys = [part.strip() for part in str(env[source]).replace(",", " ").split() if part.strip()]
    elif source == "TOKENSFREE_REAL_API_KEY_FILE":
        keys = _keys_from_file(env[source])
    elif source == "TOKENSFREE_REAL_API_KEY_STORE":
        from tokensfree.credstore import CredentialError, load_secret

        store = str(env[source]).strip()
        provider_name, _sep, alias = store.partition("/")
        try:
            keys = [load_secret(provider_name, alias=alias or "env-key")]
        except CredentialError as error:
            raise ValueError("cannot read the system credential store: %s" % error) from None
    else:
        keys = _keys_from_file(env[source])
    if not 1 <= len(keys) <= MAX_REAL_KEYS:
        raise ValueError("real provider needs 1..%d API keys" % MAX_REAL_KEYS)
    if any(not key or len(key) > 512 for key in keys):
        raise ValueError("invalid API key length")
    return tuple(keys)


def _snapshot_expiry(path, *, provider, model):
    """Expiry from a recorded evidence snapshot, bound to the configured model/provider."""
    try:
        raw = pathlib.Path(str(path)).read_text(encoding="utf-8")
    except OSError:
        raise ValueError("cannot read evidence snapshot") from None
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("evidence snapshot is not valid JSON") from None
    if not isinstance(data, dict):
        raise ValueError("evidence snapshot must be a JSON object")
    snapshot_model = str(data.get("model", "")).strip()
    if not snapshot_model:
        raise ValueError("evidence snapshot has no model")
    if snapshot_model.lower() != model.strip().lower():
        raise ValueError("evidence snapshot is for model %r, configured %r" % (snapshot_model, model))
    snapshot_provider = str(data.get("provider", "")).strip()
    if snapshot_provider and snapshot_provider.lower() != provider.strip().lower():
        raise ValueError("evidence snapshot is for provider %r, configured %r" % (snapshot_provider, provider))
    expires_at = data.get("evidence_expires_at")
    if type(expires_at) not in (int, float) or isinstance(expires_at, bool):
        raise ValueError("evidence snapshot has no numeric evidence_expires_at")
    return float(expires_at)


def evidence_expiry_from_env(env, *, provider, model):
    inline = str(env.get("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT", "")).strip()
    snapshot = str(env.get("TOKENSFREE_REAL_EVIDENCE_SNAPSHOT", "")).strip()
    if inline and snapshot:
        raise ValueError(
            "set only one of TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT and TOKENSFREE_REAL_EVIDENCE_SNAPSHOT"
        )
    if inline:
        try:
            return float(inline)
        except ValueError:
            raise ValueError("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT must be a unix timestamp") from None
    if not snapshot:
        raise ValueError(
            "missing real-provider environment: TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT"
            " (or TOKENSFREE_REAL_EVIDENCE_SNAPSHOT)"
        )
    return _snapshot_expiry(snapshot, provider=provider, model=model)


def real_specs_from_env(env, *, clock=time.time):
    """One ProviderSpec per key; extra keys become provider-2, provider-3, ..."""
    skipped = EVIDENCE_SOURCE_VARS + ("TOKENSFREE_REAL_API_KEY",)
    base_missing = [
        key for key in REAL_ENV_KEYS if key not in skipped and not str(env.get(key, "")).strip()
    ]
    keys_missing = not any(str(env.get(name, "")).strip() for name in KEY_SOURCE_VARS)
    evidence_missing = not any(str(env.get(name, "")).strip() for name in EVIDENCE_SOURCE_VARS)
    if base_missing or keys_missing or evidence_missing:
        names = list(base_missing)
        if keys_missing:
            names.append("TOKENSFREE_REAL_API_KEY (or _API_KEYS / _API_KEY_FILE / _API_KEYS_FILE)")
        if evidence_missing:
            names.append("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT (or TOKENSFREE_REAL_EVIDENCE_SNAPSHOT)")
        raise ValueError("missing real-provider environment: " + ", ".join(names))
    keys = real_keys_from_env(env)
    provider = str(env["TOKENSFREE_REAL_PROVIDER"]).strip()
    model = str(env["TOKENSFREE_REAL_MODEL"]).strip()
    if not provider or not model:
        raise ValueError("provider and model must be non-empty")
    expires_at = evidence_expiry_from_env(env, provider=provider, model=model)
    now = clock()
    if expires_at <= now:
        raise ValueError("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT is in the past; refresh the free-tier evidence")
    specs = []
    for index, key in enumerate(keys):
        name = provider if index == 0 else "%s-%d" % (provider, index + 1)
        specs.append(
            ProviderSpec(name, model, "free_verified", expires_at, REAL_DEFAULT_CAPABILITIES, simulation=False)
        )
    return tuple(specs), keys


def real_spec_from_env(env, *, clock=time.time):
    """Build the real ProviderSpec from an explicit environment mapping.

    Fails closed with the exact missing/invalid variable names. The API key is
    never returned in error messages.
    """
    specs, _keys = real_specs_from_env(env, clock=clock)
    return specs[0]


def build_real_runtime(*, config, env, clock=time.time, storage=None, timeout=15.0, max_stream_s=120.0):
    """Runtime for one real OpenAI-compatible provider, configured from the environment.

    Building it does not mean calling it: admission still requires
    TOKENSFREE_ENABLE_REAL=1 plus valid evidence.
    """
    specs, keys = real_specs_from_env(env, clock=clock)
    base_url = str(env["TOKENSFREE_REAL_BASE_URL"]).strip()
    account_ids = None
    accounts = getattr(storage, "accounts", None)
    if accounts is not None:
        account_ids = {}
        for spec, key in zip(specs, keys):
            fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
            account_id = accounts.ensure(
                spec.name,
                "env-key",
                "env:" + spec.name,
                fingerprint,
                now=clock(),
                tier="free_verified",
                status="active",
            )
            row = accounts.get(account_id)
            if row is not None and row["fingerprint"] != fingerprint:
                # The operator replaced this key: treat the account as fresh again.
                accounts.refresh_credentials(account_id, fingerprint, status="active")
            account_ids[spec.name] = account_id
    adapters = {
        spec.name: OpenAICompatAdapter(
            base_url,
            key,
            timeout=timeout,
            max_stream_s=max_stream_s,
            allow_remote=True,
        )
        for spec, key in zip(specs, keys)
    }
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=specs,
            adapters=adapters,
            authorized=frozenset(spec.name for spec in specs),
            clock=clock,
            storage=storage,
            rotation=RoundRobin(),
            account_ids=account_ids,
            accounts=accounts,
        ),
        storage=storage,
    )


def build_profiles_runtime(
    profiles,
    *,
    config,
    storage=None,
    clock=time.time,
    key_loader=None,
    timeout=30.0,
    max_stream_s=120.0,
):
    """Build a multi-channel runtime from user-defined provider profiles."""
    enabled = []
    for profile in profiles:
        profile = provider_profiles.validate_profile(profile)
        if profile.enabled:
            enabled.append(profile)
    if not enabled:
        raise ValueError("no enabled provider profiles")
    loader = key_loader or (lambda profile_id: provider_profiles.load_key(profile_id))
    allow_paid = bool(getattr(config, "allow_user_paid", False))
    specs = []
    adapters = {}
    by_name = {}
    keys = {}
    for profile in enabled:
        try:
            key = loader(profile.id)
        except Exception:
            if profile.cost_policy != "zero_cost_local":
                raise ValueError("cannot load the key for profile %r" % profile.id) from None
            key = ""
        if not key:
            if profile.cost_policy != "zero_cost_local":
                raise ValueError("profile %r has no stored key" % profile.id)
            key = "local-no-key"
        expires_at = 0.0
        if profile.cost_policy == "free_verified":
            expires_at = float(provider_profiles.resolve_evidence(profile, clock=clock))
        spec = ProviderSpec(
            profile.id,
            profile.model,
            profile.cost_policy,
            expires_at,
            profile.capabilities,
            simulation=False,
        )
        adapter = OpenAICompatAdapter(
            profile.base_url,
            key,
            timeout=timeout,
            max_stream_s=max_stream_s,
            allow_remote=True,
            allow_private_http=(profile.cost_policy == "zero_cost_local"),
        )
        specs.append(spec)
        adapters[profile.id] = adapter
        by_name[profile.id] = profile
        keys[profile.id] = key

    account_ids = None
    accounts = getattr(storage, "accounts", None)
    if accounts is not None:
        account_ids = {}
        for profile in enabled:
            fingerprint = hashlib.sha256(keys[profile.id].encode("utf-8")).hexdigest()
            account_ids[profile.id] = accounts.ensure(
                profile.id,
                "profile-key",
                "cred:" + provider_profiles.key_ref_for(profile.id),
                fingerprint,
                now=clock(),
                tier=profile.cost_policy,
                status="active",
            )

    def profiles_gate(spec):
        profile = by_name.get(spec.name)
        if profile is None:
            raise AdmissionDenied("unknown_profile")
        if not config.enable_real:
            raise AdmissionDenied("real_disabled")
        if profile.cost_policy == "free_verified":
            try:
                provider_profiles.resolve_evidence(profile, clock=clock)
            except provider_profiles.ProfileError:
                raise AdmissionDenied("expired_free_evidence") from None
        elif profile.cost_policy == "user_paid" and not allow_paid:
            raise AdmissionDenied("user_paid_disabled")
        if account_ids and accounts is not None:
            row = accounts.get(account_ids[profile.id])
            if row is not None:
                if row["status"] != "active":
                    raise AdmissionDenied("account_" + str(row["status"]))
                cooldown_until = row["cooldown_until"]
                if cooldown_until is not None and cooldown_until > clock():
                    raise AdmissionDenied("account_cooldown")
        return Admission(
            provider=spec.name,
            capabilities=spec.capabilities,
            simulation=bool(getattr(spec, "simulation", True)),
            _token=_ADMISSION_TOKEN,
        )

    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=tuple(specs),
            adapters=adapters,
            authorized=frozenset(profile.id for profile in enabled),
            clock=clock,
            storage=storage,
            rotation=RoundRobin(),
            account_ids=account_ids,
            accounts=accounts,
            gate=profiles_gate,
        ),
        storage=storage,
    )