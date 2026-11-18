"""Upstream transport: stdlib loopback SSE client plus full streaming end-to-end."""
import asyncio
import json

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.transport import LoopbackSSETransport, LoopbackStreamingAdapter
from tokensfree.config import load_config
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.router.admission import admit
from upstream_support import SSEUpstream


@pytest.fixture()
def upstream():
    server = SSEUpstream()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def dripping_upstream():
    server = SSEUpstream(delay_s=0.2)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def adapter_request():
    from tokensfree.adapters.base import ChatRequest

    return ChatRequest(model="fixture-model", messages=({"role": "user", "content": "hi"},))


def test_transport_reads_sse_frames(upstream):
    transport = LoopbackSSETransport(upstream.port)
    lines = list(transport.iter_lines(b'{"model":"fixture-model"}'))
    joined = "".join(lines)
    assert "[DONE]" in joined


def test_adapter_stream_yields_parsed_events(upstream):
    from tokensfree.adapters.base import Admission, _ADMISSION_TOKEN

    adapter = LoopbackStreamingAdapter(upstream.port)
    admission = Admission(provider="loopback", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)

    async def run_all():
        return [event async for event in adapter.stream(adapter_request(), admission)]

    events = asyncio.run(run_all())
    assert [event.kind for event in events] == ["delta", "delta", "delta", "finish", "done"]
    assert events[1].delta == {"content": "he"}


def test_upstream_429_without_retry_after_is_quota_exhausted(upstream):
    upstream.configure(status=429, body="{}", content_type="application/json")
    with pytest.raises(NormalizedError) as caught:
        list(LoopbackSSETransport(upstream.port).iter_lines(b"{}"))
    assert caught.value.code == "quota_exhausted"
    assert caught.value.retryable is False


def test_upstream_429_with_retry_after_is_retryable(upstream):
    upstream.configure(status=429, body="{}", content_type="application/json", headers={"Retry-After": "5"})
    with pytest.raises(NormalizedError) as caught:
        list(LoopbackSSETransport(upstream.port).iter_lines(b"{}"))
    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True


def test_full_streaming_path_to_client_frames(upstream):
    config = load_config({})
    adapter = LoopbackStreamingAdapter(upstream.port)
    spec = ProviderSpec("loopback", "fixture-model", "mock_zero_cost", 200, frozenset({"text", "stream"}))
    services = type("S", (), {})()
    services.specs = (spec,)
    services.adapters = {"loopback": adapter}
    services.authorized = frozenset({"loopback"})
    services.admit = lambda instance: admit(instance, config, frozenset({"loopback"}), 100)
    payload = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}

    plan = prepare_stream(payload, services=services, request_id="req-e2e")
    frames = []

    async def run_all():
        async for frame in iter_frames(plan, admit=services.admit):
            frames.append(frame)

    asyncio.run(run_all())
    decoded = [frame.decode("utf-8") for frame in frames]
    contents = [
        json.loads(line[len("data: "):])["choices"][0]["delta"].get("content")
        for line in decoded
        if line.startswith("data: {") and "content" in line
    ]
    assert [value for value in contents if value] == ["he", "llo"]
    assert decoded[-1].strip() == "data: [DONE]"


def test_transport_rejects_non_loopback_shapes():
    for port in ("80", 0, 65536, True):
        with pytest.raises(ValueError):
            LoopbackSSETransport(port)
    with pytest.raises(ValueError):
        LoopbackSSETransport(8765, path="https://evil.example/v1")


def test_malformed_frame_raises(upstream):
    from tokensfree.adapters.base import Admission, _ADMISSION_TOKEN

    upstream.configure(body='data: {not json}\n\n')
    adapter = LoopbackStreamingAdapter(upstream.port)
    admission = Admission(provider="loopback", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)

    async def run_all():
        async for _ in adapter.stream(adapter_request(), admission):
            pass

    with pytest.raises(NormalizedError) as caught:
        asyncio.run(run_all())
    assert caught.value.code == "invalid_response"


class RecordingTransport:
    def __init__(self):
        self.bodies = []

    def iter_lines(self, body):
        self.bodies.append(json.loads(body.decode("utf-8")))
        yield 'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n'
        yield "data: [DONE]\n"


def test_streaming_adapter_forwards_tools():
    from tokensfree.adapters.base import Admission, ChatRequest, _ADMISSION_TOKEN

    transport = RecordingTransport()
    adapter = LoopbackStreamingAdapter(1234, transport=transport)
    admission = Admission(provider="loopback", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)
    tools = ({"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}},)
    request = ChatRequest(model="fixture-model", messages=({"role": "user", "content": "x"},), tools=tools, tool_choice="auto")

    async def run_all():
        return [event async for event in adapter.stream(request, admission)]

    asyncio.run(run_all())
    sent = transport.bodies[0]
    assert sent["tools"][0]["function"]["name"] == "read"
    assert sent["tool_choice"] == "auto"
    assert sent["stream"] is True



def test_stream_deadline_stops_a_dripping_upstream(dripping_upstream):
    transport = LoopbackSSETransport(dripping_upstream.port, max_stream_s=0.01)
    with pytest.raises(NormalizedError) as caught:
        list(transport.iter_lines(b"{}"))
    assert caught.value.code == "timeout"
    assert caught.value.retryable is True
