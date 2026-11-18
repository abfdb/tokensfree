"""Runtime assembly.

Turns configuration plus adapter instances into the Services the edge core needs.
Opening a socket and serving HTTP stay outside this module.
"""
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


def build_services(*, config, specs, adapters, authorized, clock=time.time, storage=None, breakers=None):
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


def real_spec_from_env(env, *, clock=time.time):
    """Build the real ProviderSpec from an explicit environment mapping.

    Fails closed with the exact missing/invalid variable names. The API key is
    never returned in error messages.
    """
    missing = [key for key in REAL_ENV_KEYS if not str(env.get(key, "")).strip()]
    if missing:
        raise ValueError("missing real-provider environment: " + ", ".join(missing))
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
        raise ValueError("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT is in the past; refresh the free-tier evidence first")
    return ProviderSpec(
        provider,
        model,
        "free_verified",
        expires_at,
        REAL_DEFAULT_CAPABILITIES,
        simulation=False,
    )


def build_real_runtime(*, config, env, clock=time.time, storage=None, timeout=15.0, max_stream_s=120.0):
    """Runtime for one real OpenAI-compatible provider, configured from the environment.

    Building it does not mean calling it: admission still requires
    TOKENSFREE_ENABLE_REAL=1 plus valid evidence.
    """
    spec = real_spec_from_env(env, clock=clock)
    adapter = OpenAICompatAdapter(
        str(env["TOKENSFREE_REAL_BASE_URL"]).strip(),
        str(env["TOKENSFREE_REAL_API_KEY"]).strip(),
        timeout=timeout,
        max_stream_s=max_stream_s,
        allow_remote=True,
    )
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=(spec,),
            adapters={spec.name: adapter},
            authorized=frozenset({spec.name}),
            clock=clock,
            storage=storage,
        ),
        storage=storage,
    )
