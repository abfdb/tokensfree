"""ASGI adapter: the same contract, exercised without any ASGI server."""
import asyncio
import json

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.adapters.base import NormalizedError
from tokensfree.edge.app_core import Services
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from tokensfree.router.admission import admit
from tokensfree.router.execute import NoProviderAvailable

TOKEN = "c" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PLAIN = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
STREAMING = ProviderSpec("stream-alpha", "mock-stream", "mock_zero_cost", 200, frozenset({"text", "stream"}))
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class FakeAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="hello asgi")


class FakeStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        yield StreamEvent("delta", {"role": "assistant"})
        yield StreamEvent("delta", {"content": "sse"})
        yield StreamEvent("finish", finish_reason="stop")


def make_backend(specs, adapters):
    names = frozenset(spec.name for spec in specs)
    services = Services(
        specs=tuple(specs),
        adapters=adapters,
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
    )
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)


def app_for(specs=(PLAIN,), adapters=None):
    adapters = adapters if adapters is not None else {spec.name: FakeAdapter() for spec in specs}
    return make_asgi_app(make_backend(specs, adapters))


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
    start = sent[0]
    status = start["status"]
    response_headers = {name.decode("latin-1"): value.decode("latin-1") for name, value in start["headers"]}
    payload = b"".join(message["body"] for message in sent[1:])
    return status, response_headers, payload, sent[1:]


def test_models_through_asgi():
    status, headers, body, _ = call(app_for(), "GET", "/v1/models")
    assert status == 200
    assert headers["content-type"].startswith("application/json")
    assert json.loads(body)["data"][0]["id"] == "mock-a"


def test_unknown_path_and_wrong_method():
    status, _, body, _ = call(app_for(), "GET", "/nope")
    assert status == 404 and json.loads(body)["error"]["code"] == "not_found"
    status, headers, body, _ = call(app_for(), "POST", "/v1/models")
    assert status == 405 and headers["allow"] == "GET"


def test_host_and_token_checks():
    status, _, _, _ = call(app_for(), "GET", "/v1/models", headers={"host": "evil.example"})
    assert status == 403
    status, _, _, _ = call(app_for(), "GET", "/v1/models", headers={"authorization": "Bearer nope"})
    assert status == 401


def test_chat_through_asgi():
    status, _, body, _ = call(app_for(), "POST", "/v1/chat/completions", body=json.dumps(PAYLOAD).encode())
    assert status == 200
    assert json.loads(body)["choices"][0]["message"]["content"] == "hello asgi"


def test_streaming_through_asgi():
    app = app_for(specs=(STREAMING,), adapters={"stream-alpha": FakeStreamAdapter()})
    status, headers, body, messages = call(app, "POST", "/v1/chat/completions",
                                           body=json.dumps(dict(PAYLOAD, stream=True)).encode())
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    assert [message["more_body"] for message in messages].count(True) >= 2
    assert body.decode("utf-8").strip().endswith("data: [DONE]")


def test_oversized_body_is_413():
    app = app_for()
    limit = CONFIG.max_body_bytes
    status, _, body, _ = call(app, "POST", "/v1/chat/completions", body=b"x" * (limit + 1))
    assert status == 413
    assert json.loads(body)["error"]["code"] == "payload_too_large"



class EmptyStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        raise NoProviderAvailable((("alpha", "upstream_disconnected"),))
        yield  # pragma: no cover


class FailingStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        raise NormalizedError("upstream_error", retryable=True)
        yield  # pragma: no cover


def test_stream_without_any_provider_returns_a_json_error():
    app = app_for(specs=(STREAMING,), adapters={"stream-alpha": EmptyStreamAdapter()})
    status, headers, body, _ = call(app, "POST", "/v1/chat/completions", body=json.dumps(dict(PAYLOAD, stream=True)).encode())
    assert status == 503
    assert headers["content-type"].startswith("application/json")
    assert json.loads(body)["error"]["code"] == "no_free_provider"


def test_stream_failing_before_first_byte_returns_a_json_error():
    app = app_for(specs=(STREAMING,), adapters={"stream-alpha": FailingStreamAdapter()})
    status, headers, body, _ = call(app, "POST", "/v1/chat/completions", body=json.dumps(dict(PAYLOAD, stream=True)).encode())
    assert status == 502
    assert headers["content-type"].startswith("application/json")
    assert json.loads(body)["error"]["code"] == "upstream_error"
