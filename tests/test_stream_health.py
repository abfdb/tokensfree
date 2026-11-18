"""Circuit breaker semantics on the streaming path and in Services wiring."""
import asyncio

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.router.admission import admit
from tokensfree.router.execute import NoProviderAvailable
from tokensfree.router.health import CircuitBreaker

CONFIG = load_config({})
STREAMING = frozenset({"text", "stream"})
ALPHA = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200, STREAMING)
BETA = ProviderSpec("beta", "m-b", "mock_zero_cost", 200, STREAMING)
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}


class FakeStreamAdapter:
    capabilities = STREAMING

    def __init__(self, events):
        self.events = list(events)
        self.calls = 0

    async def stream(self, request, admission):
        self.calls += 1
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


def service(*pairs):
    specs = tuple(spec for spec, _ in pairs)
    adapters = {spec.name: adapter for spec, adapter in pairs}
    names = frozenset(adapters)

    def gate(provider_spec):
        return admit(provider_spec, CONFIG, names, 100)

    return Services(specs=specs, adapters=adapters, admit=gate, authorized=names)


def collect(plan, *, admit_fn, breaker_for=None, clock=None):
    frames = []
    kwargs = {"admit": admit_fn, "breaker_for": breaker_for}
    if clock is not None:
        kwargs["clock"] = clock

    async def run_all():
        async for frame in iter_frames(plan, **kwargs):
            frames.append(frame)

    asyncio.run(run_all())
    return frames


def test_open_breaker_skips_candidate_without_connecting():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(1000)
    first = FakeStreamAdapter([StreamEvent("finish", finish_reason="stop")])
    second = FakeStreamAdapter([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="stop")])
    services = service((ALPHA, first), (BETA, second))
    plan = prepare_stream(PAYLOAD, services=services, request_id="req-1")
    frames = collect(plan, admit_fn=services.admit,
                    breaker_for=lambda spec: breaker if spec.name == "alpha" else None,
                    clock=lambda: 1001)
    assert frames
    assert first.calls == 0
    assert second.calls == 1


def test_all_circuits_open_raises_without_connecting():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(1000)
    adapter = FakeStreamAdapter([])
    services = service((ALPHA, adapter))
    plan = prepare_stream(PAYLOAD, services=services, request_id="req-1")
    with pytest.raises(NoProviderAvailable) as caught:
        collect(plan, admit_fn=services.admit, breaker_for=lambda spec: breaker, clock=lambda: 1001)
    assert caught.value.attempts == (("alpha", "circuit_open"),)
    assert adapter.calls == 0


def test_stream_failure_feeds_the_breaker():
    breaker = CircuitBreaker(threshold=1, open_s=60)
    adapter = FakeStreamAdapter([NormalizedError("upstream_error", retryable=False)])
    services = service((ALPHA, adapter))
    plan = prepare_stream(PAYLOAD, services=services, request_id="req-1")
    with pytest.raises(NormalizedError):
        collect(plan, admit_fn=services.admit, breaker_for=lambda spec: breaker, clock=lambda: 1001)
    assert breaker.state == "open"


def test_services_breakers_are_used_by_the_non_streaming_core():
    import time as _time

    breaker = CircuitBreaker(threshold=1, open_s=60)
    breaker.record_failure(_time.time())
    alpha = FakeStreamAdapter([])

    class TextAdapter:
        capabilities = frozenset({"text"})
        calls = 0

        async def complete(self, request, admission):
            TextAdapter.calls += 1
            from tokensfree.adapters.base import ChatResult
            return ChatResult(content="beta wins")

    services = Services(
        specs=(ALPHA, BETA),
        adapters={"alpha": TextAdapter(), "beta": TextAdapter()},
        admit=lambda spec: admit(spec, CONFIG, frozenset({"alpha", "beta"}), 100),
        authorized=frozenset({"alpha", "beta"}),
        breakers={"alpha": breaker},
    )
    outcome = asyncio.run(handle_chat({"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]},
                                      services=services, request_id="req-1"))
    assert outcome.status == 200
    assert outcome.provider == "beta"
    assert TextAdapter.calls == 1
    assert alpha.calls == 0
