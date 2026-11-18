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


@dataclass(frozen=True)
class Runtime:
    config: AppConfig
    services: Services


def build_services(*, config, specs, adapters, authorized, clock=time.time):
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
    )


def build_loopback_runtime(port, *, config=None, clock=time.time, ttl_s=60.0):
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
        ),
    )


def build_mock_runtime(*, config=None, clock=time.time, ttl_s=60.0):
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
        ),
    )


def build_loopback_streaming_runtime(port, *, config=None, clock=time.time, ttl_s=60.0, timeout=5.0):
    """Runtime whose single provider speaks SSE to a local fixture upstream."""
    config = load_config({}) if config is None else config
    spec = ProviderSpec("loopback", "fixture-model", "mock_zero_cost", clock() + ttl_s, frozenset({"text", "stream"}))
    return Runtime(
        config=config,
        services=build_services(
            config=config,
            specs=(spec,),
            adapters={"loopback": LoopbackStreamingAdapter(port, timeout=timeout)},
            authorized=frozenset({"loopback"}),
            clock=clock,
        ),
    )
