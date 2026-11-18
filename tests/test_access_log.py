"""One structured access-log line per request, never containing the body."""
import asyncio
import io
import json

import pytest

from providers import ProviderSpec
from tokensfree import obs
from tokensfree.adapters.base import ChatResult
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from tokensfree.router.admission import admit

TOKEN = "l" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PLAIN = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 10 ** 9)
STREAMING = ProviderSpec("stream-alpha", "mock-stream", "mock_zero_cost", 10 ** 9, frozenset({"text", "stream"}))
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "secret-prompt-text"}]}


class FakeAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="answer")


class FakeStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        yield StreamEvent("delta", {"content": "streamed"})
        yield StreamEvent("finish", finish_reason="stop")


def app_for(specs, adapters):
    names = frozenset(spec.name for spec in specs)
    services = Services(
        specs=tuple(specs),
        adapters=adapters,
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
    )
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)
    return make_asgi_app(backend)


def call(app, method, path, *, body=b"", headers=None):
    merged = [("host", "127.0.0.1:8765"), ("authorization", "Bearer " + TOKEN)]
    merged.extend((name.lower(), value) for name, value in (headers or {}).items())
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(name.encode("latin-1"), value.encode("latin-1")) for name, value in merged],
    }
    sent = []
    chunks = [body] if body else []

    async def receive():
        return {"type": "http.request", "body": chunks.pop(0) if chunks else b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    status = sent[0]["status"]
    response_headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in sent[0]["headers"]}
    payload = b"".join(message.get("body", b"") for message in sent[1:])
    return status, response_headers, payload


@pytest.fixture()
def log_buffer():
    buffer = io.StringIO()
    obs.configure_logging(stream=buffer)
    try:
        yield buffer
    finally:
        obs.configure_logging(stream=None)


def lines(buffer):
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_access_log_records_request_metadata_without_the_body(log_buffer):
    app = app_for((PLAIN,), {"alpha": FakeAdapter()})
    status, headers, body = call(app, "POST", "/v1/chat/completions", body=json.dumps(PAYLOAD).encode())
    assert status == 200

    records = lines(log_buffer)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "http_request"
    assert record["method"] == "POST"
    assert record["path"] == "/v1/chat/completions"
    assert record["status"] == 200
    assert record["request_id"] == headers["x-request-id"]
    assert isinstance(record["latency_ms"], int) and record["latency_ms"] >= 0
    assert "secret-prompt-text" not in log_buffer.getvalue()
    assert "answer" not in log_buffer.getvalue()


def test_access_log_records_rejections(log_buffer):
    app = app_for((PLAIN,), {"alpha": FakeAdapter()})
    call(app, "GET", "/nope")
    status, _, _ = call(app, "POST", "/v1/chat/completions", body=b"x" * (CONFIG.max_body_bytes + 1))
    assert status == 413
    statuses = [(record["path"], record.get("status")) for record in lines(log_buffer)]
    assert statuses == [("/nope", 404), ("/v1/chat/completions", 413)]


def test_access_log_records_streaming_once(log_buffer):
    app = app_for((STREAMING,), {"stream-alpha": FakeStreamAdapter()})
    status, headers, body = call(
        app, "POST", "/v1/chat/completions", body=json.dumps(dict(PAYLOAD, stream=True)).encode()
    )
    assert status == 200
    assert b"[DONE]" in body
    records = lines(log_buffer)
    assert len(records) == 1
    assert records[0]["status"] == 200
    assert records[0]["request_id"] == headers["x-request-id"]
    assert "streamed" not in log_buffer.getvalue()


def test_x_request_id_correlates_header_and_body():
    app = app_for((PLAIN,), {"alpha": FakeAdapter()})
    status, headers, body = call(app, "POST", "/v1/chat/completions", body=json.dumps(PAYLOAD).encode())
    assert status == 200
    payload = json.loads(body)
    assert headers["x-request-id"] == payload["id"]

    status, headers, body = call(app, "GET", "/nope")
    assert status == 404
    assert headers["x-request-id"] == json.loads(body)["error"]["request_id"]
