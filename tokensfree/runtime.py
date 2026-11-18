"""Runtime assembly.

Turns configuration plus adapter instances into the Services the edge core needs.
Opening a socket and serving HTTP stay outside this module.
"""
import pathlib
import time
from dataclasses import dataclass

from providers import ProviderSpec
from tokensfree.adapters.loopback import LoopbackAdapter
from tokensfree.adapters.openai_compat import OpenAICompatAdapter
from tokensfree.adapters.transport import LoopbackStreamingAdapter
from tokensfree.adapters.mock import MockChatAdapter
from tokensfree.config import AppConfig, load_config
from tokensfree.edge.app_core import Services
from tokensfree.router.admission import admit
from tokensfree.router.health import CircuitBreaker
from tokensfree.router.scheduling import RoundRobin
from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import EventsRepo, IdempotencyRepo, LedgerRepo

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
REAL_DEFAULT_CAPABILITIES = frozenset({"text", "stream", "tools"})


@dataclass(frozen=True)
class Storage:
    """SQLite-backed idempotency, ledger and events for the runnable server."""

    connection: object
    idempotency: object
    ledger: object
    events: object

    @classmethod
    def open(cls, path=":memory:"):
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        connection = connect(path)
        migrate(connection)
        return cls(
            connection=connection,
            idempotency=IdempotencyRepo(connection),
            ledger=LedgerRepo(connection),
            events=EventsRepo(connection),
        )

    def close(self):
        self.connection.close()


@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    services: Services
    storage: object = None


def build_services(*, config, specs, adapters, authorized, clock=time.time, storage=None, breakers=None, rotation=None):
    specs = tuple(specs)
    names = {spec.name for spec in specs}
    if set(adapters) - names:
        raise ValueError("adapter registered without a provider spec")
    if not frozenset(authorized) <= names:
        raise ValueError("authorized provider has no spec")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if breakers is None:
        breakers = {spec.name: CircuitBreaker() for spec in specs}
    else:
        breakers = dict(breakers)
        unknown = set(breakers) - names
        if unknown:
            raise ValueError("breaker registered without a provider spec: " + ", ".join(sorted(unknown)))

    def gate(spec):
        return admit(spec, config, frozenset(authorized), clock())

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
    else:
        keys = _keys_from_file(env[source])
    if not 1 <= len(keys) <= MAX_REAL_KEYS:
        raise ValueError("real provider needs 1..%d API keys" % MAX_REAL_KEYS)
    if any(not key or len(key) > 512 for key in keys):
        raise ValueError("invalid API key length")
    return tuple(keys)


def real_specs_from_env(env, *, clock=time.time):
    """One ProviderSpec per key; extra keys become provider-2, provider-3, ..."""
    base_missing = [
        key for key in REAL_ENV_KEYS if key != "TOKENSFREE_REAL_API_KEY" and not str(env.get(key, "")).strip()
    ]
    keys_missing = not (
        str(env.get("TOKENSFREE_REAL_API_KEY", "")).strip()
        or str(env.get("TOKENSFREE_REAL_API_KEYS", "")).strip()
    )
    if base_missing or keys_missing:
        names = list(base_missing)
        if keys_missing:
            names.append("TOKENSFREE_REAL_API_KEY (or TOKENSFREE_REAL_API_KEYS)")
        raise ValueError("missing real-provider environment: " + ", ".join(names))
    keys = real_keys_from_env(env)
    provider = str(env["TOKENSFREE_REAL_PROVIDER"]).strip()
    model = str(env["TOKENSFREE_REAL_MODEL"]).strip()
    if not provider or not model:
        raise ValueError("provider and model must be non-empty")
    try:
        expires_at = float(env["TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT"])
    except (TypeError, ValueError):
        raise ValueError("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT must be a unix timestamp") from None
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
        ),
        storage=storage,
    )
