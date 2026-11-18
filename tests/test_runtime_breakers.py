"""The runnable runtime configures circuit breakers, so a dead provider stops being retried."""
import asyncio

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult, NormalizedError
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.router.health import CircuitBreaker
from tokensfree.runtime import build_mock_runtime

CONFIG = load_config({})
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


def test_runtimes_configure_one_breaker_per_provider():
    runtime = build_mock_runtime(config=CONFIG)
    assert set(runtime.services.breakers) == {"mock-alpha", "mock-beta"}
    assert all(isinstance(breaker, CircuitBreaker) for breaker in runtime.services.breakers.values())


def test_breaker_state_is_visible_in_status():
    from tokensfree.edge.backend import EdgeRequest, MemoryBackend

    runtime = build_mock_runtime(config=CONFIG)
    backend = MemoryBackend(config=CONFIG, services=runtime.services, port=8765, token="t" * 32)
    response = asyncio.run(
        backend.handle(
            EdgeRequest(
                method="GET",
                path="/api/status",
                headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + "t" * 32},
            )
        )
    )
    import json

    payload = json.loads(response.body)
    assert set(payload["breakers"]) == {"mock-alpha", "mock-beta"}
    assert payload["breakers"]["mock-alpha"]["state"] == "closed"


class FailingAdapter:
    capabilities = frozenset({"text"})

    def __init__(self):
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        raise NormalizedError("upstream_error", retryable=True, provider="alpha")


class GoodAdapter:
    capabilities = frozenset({"text"})

    def __init__(self):
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        return ChatResult(content="ok")


def services_with_breakers(threshold=3):
    alpha = ProviderSpec("alpha", "m-a", "mock_zero_cost", 10 ** 9)
    beta = ProviderSpec("beta", "m-b", "mock_zero_cost", 10 ** 9)
    adapters = {"alpha": FailingAdapter(), "beta": GoodAdapter()}
    names = frozenset(adapters)
    breakers = {"alpha": CircuitBreaker(threshold=threshold, open_s=60), "beta": CircuitBreaker(threshold=threshold)}
    services = Services(
        specs=(alpha, beta),
        adapters=adapters,
        admit=lambda spec: __import__("tokensfree.router.admission", fromlist=["admit"]).admit(spec, CONFIG, names, 100),
        authorized=names,
        breakers=breakers,
    )
    return services, adapters, breakers


def call(services):
    return asyncio.run(handle_chat(PAYLOAD, services=services, request_id="req-breaker"))


def test_repeated_failures_then_open_circuit_skips_the_dead_provider():
    services, adapters, breakers = services_with_breakers(threshold=2)
    first = call(services)
    assert first.status == 200 and first.provider == "beta"     # alpha failed, beta served
    alpha_calls_after_first = adapters["alpha"].calls
    second = call(services)
    assert second.status == 200 and second.provider == "beta"
    assert adapters["alpha"].calls == alpha_calls_after_first + 1
    # Third call: alpha is now open and must not be attempted at all.
    third = call(services)
    assert third.status == 200 and third.provider == "beta"
    assert adapters["alpha"].calls == alpha_calls_after_first + 1
    assert breakers["alpha"].state == "open"
    assert not any(attempt[1] == "upstream_error" for attempt in third.body["tokensfree"]["trace"])
