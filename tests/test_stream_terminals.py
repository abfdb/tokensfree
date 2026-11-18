"""A provider stream must always end in a terminal frame, or not start at all."""
import asyncio

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.router.admission import admit

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


def build(*pairs):
    specs = tuple(spec for spec, _ in pairs)
    adapters = {spec.name: adapter for spec, adapter in pairs}
    names = frozenset(adapters)
    return Services(specs=specs, adapters=adapters, admit=lambda spec: admit(spec, CONFIG, names, 100), authorized=names)


def collect(services):
    plan = prepare_stream(PAYLOAD, services=services, request_id="req-term")
    frames = []

    async def run_all():
        async for frame in iter_frames(plan, admit=services.admit):
            frames.append(frame)

    asyncio.run(run_all())
    return frames


def test_truncated_stream_after_a_frame_is_a_terminal_error():
    adapter = FakeStreamAdapter([StreamEvent("delta", {"content": "partial"})])
    frames = collect(build((ALPHA, adapter)))
    assert b"event: error" in frames[-1]
    assert b"upstream_disconnected" in frames[-1]
    assert not any(b"[DONE]" in frame for frame in frames)


def test_done_without_finish_emits_a_standard_terminal():
    adapter = FakeStreamAdapter([StreamEvent("delta", {"role": "assistant"}), StreamEvent("delta", {"content": "ok"}), StreamEvent("done")])
    frames = collect(build((ALPHA, adapter)))
    assert b"[DONE]" in frames[-1]
    joined = b"".join(frames)
    assert b'"finish_reason": "stop"' in joined


def test_empty_stream_falls_back_to_the_next_candidate():
    empty = FakeStreamAdapter([])
    good = FakeStreamAdapter([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="stop")])
    frames = collect(build((ALPHA, empty), (BETA, good)))
    assert empty.calls == 1
    assert good.calls == 1
    assert b"ok" in b"".join(frames)
    assert b"[DONE]" in frames[-1]


def test_empty_stream_alone_raises_the_last_retryable_error():
    adapter = FakeStreamAdapter([])
    services = build((ALPHA, adapter))
    plan = prepare_stream(PAYLOAD, services=services, request_id="req-term")

    async def run_all():
        async for _ in iter_frames(plan, admit=services.admit):
            pass

    with pytest.raises(NormalizedError) as caught:
        asyncio.run(run_all())
    assert caught.value.code == "upstream_disconnected"
    assert caught.value.retryable is True
