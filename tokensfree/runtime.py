"""Runtime assembly.

Turns configuration plus adapter instances into the Services the edge core needs.
Opening a socket and serving HTTP stay outside this module.
"""
import time
from dataclasses import dataclass

from providers import ProviderSpec
from tokensfree.adapters.loopback import LoopbackAdapter
from tokensfree.adapters.transport import LoopbackStreamingAdapter
from tokensfree.adapters.mock import MockChatAdapter
from tokensfree.config import AppConfig, load_config
from tokensfree.edge.app_core import Services
from tokensfree.router.admission import admit
from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import EventsRepo, IdempotencyRepo, LedgerRepo

# Fixture providers have no free-tier claim to verify; their evidence is only a
# safety assertion, so it must outlive a long-running demo server.
DEFAULT_FIXTURE_TTL_S = 10 ** 9


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


def build_services(*, config, specs, adapters, authorized, clock=time.time, storage=None):
    specs = tuple(specs)
    names = {spec.name for spec in specs}
    if set(adapters) - names:
        raise ValueError("adapter registered without a provider spec")
    if not frozenset(authorized) <= names:
        raise ValueError("authorized provider has no spec")
    if not callable(clock):
        raise TypeError("clock must be callable")

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
