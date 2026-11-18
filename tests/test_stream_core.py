"""Streaming edge core: provider chunks -> client SSE frames, with terminal rules."""
import asyncio
import json

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.stream_core import StreamFailure, iter_frames, prepare_stream
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


def services(*pairs, authorized=None):
    specs = tuple(spec for spec, _ in pairs)
    adapters = {spec.name: adapter for spec, adapter in pairs}
    names = frozenset(adapters) if authorized is None else authorized
    service = type("S", (), {})()
    service.specs = specs
    service.adapters = adapters
    service.authorized = frozenset(names)
    service.admit = lambda spec: admit(spec, CONFIG, frozenset(names), 100)
    return service


def collect(plan, service):
    frames = []
    async def run_all():
        async for frame in iter_frames(plan, admit=service.admit):
            frames.append(frame)
    asyncio.run(run_all())
    return frames


def parse_frames(frames):
    parsed = []
    for frame in frames:
        text = frame.decode("utf-8")
        if text.startswith("event: error"):
            parsed.append(("error", json.loads(text.split("data: ", 1)[1])))
        elif text.strip() == "data: [DONE]":
            parsed.append(("done", None))
        else:
            parsed.append(("chunk", json.loads(text[len("data: "):])))
    return parsed


def test_happy_path_frame_sequence():
    adapter = FakeStreamAdapter([
        StreamEvent("delta", {"role": "assistant"}),
        StreamEvent("delta", {"content": "he"}),
        StreamEvent("delta", {"content": "llo"}),
        StreamEvent("finish", finish_reason="stop"),
    ])
    service = services((ALPHA, adapter))
    plan = prepare_stream(PAYLOAD, services=service, request_id="req-1")
    parsed = parse_frames(collect(plan, service))
    kinds = [kind for kind, _ in parsed]
    assert kinds == ["chunk", "chunk", "chunk", "chunk", "done"]
    assert parsed[1][1]["choices"][0]["delta"] == {"content": "he"}
    assert parsed[3][1]["choices"][0]["finish_reason"] == "stop"


def test_tool_call_deltas_are_forwarded():
    adapter = FakeStreamAdapter([
        StreamEvent("delta", {"role": "assistant"}),
        StreamEvent("delta", {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read"}}]}),
        StreamEvent("delta", {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}),
        StreamEvent("finish", finish_reason="tool_calls"),
    ])
    service = services((ALPHA, adapter))
    plan = prepare_stream(PAYLOAD, services=service, request_id="req-1")
    parsed = parse_frames(collect(plan, service))
    tool_deltas = [chunk["choices"][0]["delta"] for kind, chunk in parsed if kind == "chunk" and "tool_calls" in chunk["choices"][0]["delta"]]
    assert tool_deltas[0]["tool_calls"][0]["id"] == "call_1"
    assert tool_deltas[1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_error_before_first_frame_falls_back():
    first = FakeStreamAdapter([NormalizedError("rate_limited", retryable=True)])
    second = FakeStreamAdapter([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="stop")])
    service = services((ALPHA, first), (BETA, second))
    plan = prepare_stream(PAYLOAD, services=service, request_id="req-1")
    parsed = parse_frames(collect(plan, service))
    assert [kind for kind, _ in parsed][-1] == "done"
    assert first.calls == 1


def test_error_after_first_frame_is_terminal():
    first = FakeStreamAdapter([
        StreamEvent("delta", {"content": "partial"}),
        NormalizedError("upstream_error", retryable=True),
    ])
    second = FakeStreamAdapter([StreamEvent("delta", {"content": "never"})])
    service = services((ALPHA, first), (BETA, second))
    plan = prepare_stream(PAYLOAD, services=service, request_id="req-1")
    parsed = parse_frames(collect(plan, service))
    assert parsed[-1][0] == "error"
    assert parsed[-1][1]["error"]["code"] == "upstream_error"
    assert second.calls == 0
    assert all(kind != "done" for kind, _ in parsed)


def test_non_stream_payload_is_rejected():
    failure = prepare_stream({"model": "x", "messages": [{"role": "user", "content": "hi"}]},
                                         services=services((ALPHA, FakeStreamAdapter([]))), request_id="req-1")
    assert isinstance(failure, StreamFailure)
    assert failure.status == 400
    assert failure.body["error"]["code"] == "invalid_request"


def test_no_streaming_provider_is_rejected():
    text_only = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200, frozenset({"text"}))
    adapter = FakeStreamAdapter([])
    service = services((text_only, adapter))
    failure = prepare_stream(PAYLOAD, services=service, request_id="req-1")
    assert isinstance(failure, StreamFailure)
    assert failure.status == 400
    assert failure.body["error"]["code"] == "unsupported_capability"


def test_invalid_payload_is_rejected_before_streaming():
    failure = prepare_stream({"stream": True}, services=services((ALPHA, FakeStreamAdapter([]))), request_id="req-1")
    assert isinstance(failure, StreamFailure)
    assert failure.status == 400
    assert failure.body["error"]["code"] == "invalid_request"
