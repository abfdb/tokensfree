"""Upstream transport: stdlib loopback SSE client plus full streaming end-to-end."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.transport import LoopbackSSETransport, LoopbackStreamingAdapter
from tokensfree.config import load_config
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.router.admission import admit

SSE_BODY = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"he"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"llo"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


class Handler(BaseHTTPRequestHandler):
    status = 200
    body = SSE_BODY
    content_type = "text/event-stream"
    extra_headers = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        payload = type(self).body.encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", type(self).content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in type(self).extra_headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def collect_events(adapter, action=None):
    async def run_all():
        events = []
        async for event in adapter.stream("request", "admission"):
            events.append(event)
        return events
    return asyncio.run(run_all())


class AdmissionStub:
    pass


def test_transport_reads_sse_frames(upstream):
    transport = LoopbackSSETransport(upstream)
    lines = list(transport.iter_lines(b'{"model":"fixture-model"}'))
    joined = "".join(lines)
    assert "hello" not in joined  # sanity: only frames we sent
    assert "[DONE]" in joined


def test_adapter_stream_yields_parsed_events(upstream):
    adapter = LoopbackStreamingAdapter(upstream)
    from tokensfree.adapters.base import Admission, _ADMISSION_TOKEN

    admission = Admission(provider="loopback", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)
    async def run_all():
        events = []
        async for event in adapter.stream(adapter_request(), admission):
            events.append(event)
        return events
    events = asyncio.run(run_all())
    assert [event.kind for event in events] == ["delta", "delta", "delta", "finish", "done"]
    assert events[1].delta == {"content": "he"}


def adapter_request():
    from tokensfree.adapters.base import ChatRequest
    return ChatRequest(model="fixture-model", messages=({"role": "user", "content": "hi"},))


def test_upstream_429_without_retry_after_is_quota_exhausted(upstream):
    Handler.status = 429
    Handler.body = "{}"
    Handler.content_type = "application/json"
    try:
        with pytest.raises(NormalizedError) as caught:
            list(LoopbackSSETransport(upstream).iter_lines(b"{}"))
    finally:
        Handler.status = 200
        Handler.body = SSE_BODY
        Handler.content_type = "text/event-stream"
    assert caught.value.code == "quota_exhausted"
    assert caught.value.retryable is False


def test_upstream_429_with_retry_after_is_retryable(upstream):
    Handler.status = 429
    Handler.body = "{}"
    Handler.content_type = "application/json"
    Handler.extra_headers = {"Retry-After": "5"}
    try:
        with pytest.raises(NormalizedError) as caught:
            list(LoopbackSSETransport(upstream).iter_lines(b"{}"))
    finally:
        Handler.status = 200
        Handler.body = SSE_BODY
        Handler.content_type = "text/event-stream"
        Handler.extra_headers = {}
    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True


def test_full_streaming_path_to_client_frames(upstream):
    config = load_config({})
    adapter = LoopbackStreamingAdapter(upstream)
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

    Handler.body = 'data: {not json}\n\n'
    adapter = LoopbackStreamingAdapter(upstream)
    admission = Admission(provider="loopback", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)

    async def run_all():
        async for _ in adapter.stream(adapter_request(), admission):
            pass

    try:
        with pytest.raises(NormalizedError) as caught:
            asyncio.run(run_all())
    finally:
        Handler.body = SSE_BODY
    assert caught.value.code == "invalid_response"
