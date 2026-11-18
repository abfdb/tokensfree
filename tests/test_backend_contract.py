"""Backend contract: anything implementing Backend must pass these tests."""
import asyncio
import json

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.edge.app_core import Services
from tokensfree.router.admission import admit

TOKEN = "a" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
ALPHA = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
STREAM_ALPHA = ProviderSpec("stream-alpha", "mock-stream", "mock_zero_cost", 200, frozenset({"text", "stream"}))
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class FakeAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="hello there")


class FakeStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        yield StreamEvent("delta", {"role": "assistant"})
        yield StreamEvent("delta", {"content": "hi"})
        yield StreamEvent("finish", finish_reason="stop")


def build_backend(specs=(ALPHA,), adapters=None, port=8765):
    adapters = adapters if adapters is not None else {spec.name: FakeAdapter() for spec in specs}
    names = frozenset(spec.name for spec in specs)

    def gate(spec):
        return admit(spec, CONFIG, names, 100)

    services = Services(specs=tuple(specs), adapters=adapters, admit=gate, authorized=names)
    return MemoryBackend(config=CONFIG, services=services, port=port, token=TOKEN)


def request(backend, method, path, *, body=None, headers=None, host=None):
    merged = {
        "host": host if host is not None else "127.0.0.1:" + str(backend.port),
        "authorization": "Bearer " + TOKEN,
        "content-type": "application/json",
    }
    merged.update(headers or {})
    raw = b"" if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    return asyncio.run(backend.handle(EdgeRequest(method=method, path=path, headers=merged, body=raw)))


def json_body(response):
    return json.loads(response.body.decode("utf-8"))


def test_models_endpoint():
    response = request(build_backend(), "GET", "/v1/models")
    assert response.status == 200
    assert response.headers["content-type"].startswith("application/json")
    payload = json_body(response)
    assert payload["object"] == "list"
    assert payload["data"][0]["id"] == "mock-a"


def test_unknown_path_is_404_with_code():
    response = request(build_backend(), "GET", "/nope")
    assert response.status == 404
    assert json_body(response)["error"]["code"] == "not_found"


def test_wrong_method_is_405_with_allow():
    response = request(build_backend(), "POST", "/v1/models")
    assert response.status == 405
    assert response.headers["allow"] == "GET"
    assert json_body(response)["error"]["code"] == "method_not_allowed"


def test_bad_host_rejected_before_auth():
    response = request(build_backend(), "GET", "/v1/models", host="evil.example")
    assert response.status == 403
    assert json_body(response)["error"]["code"] == "origin_rejected"


def test_missing_or_wrong_token_rejected():
    for header in ("", "Bearer wrong", "Basic " + TOKEN):
        response = request(build_backend(), "GET", "/v1/models", headers={"authorization": header})
        assert response.status == 401
        assert json_body(response)["error"]["code"] == "unauthorized"


def test_non_streaming_chat():
    response = request(build_backend(), "POST", "/v1/chat/completions", body=PAYLOAD)
    assert response.status == 200
    payload = json_body(response)
    assert payload["choices"][0]["message"]["content"] == "hello there"
    assert payload["usage"]["estimated"] is True
    assert payload["id"].startswith("req-")


def test_invalid_json_body():
    response = request(build_backend(), "POST", "/v1/chat/completions", body=b"{broken")
    assert response.status == 400
    assert json_body(response)["error"]["code"] == "invalid_request"


def test_streaming_chat_returns_sse_frames():
    backend = build_backend(specs=(STREAM_ALPHA,), adapters={"stream-alpha": FakeStreamAdapter()})
    response = request(backend, "POST", "/v1/chat/completions", body=dict(PAYLOAD, stream=True))
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.stream is not None

    async def collect():
        return [chunk async for chunk in response.stream]

    frames = asyncio.run(collect())
    decoded = [frame.decode("utf-8") for frame in frames]
    assert decoded[-1].strip() == "data: [DONE]"
    assert any('"content": "hi"' in frame for frame in decoded)


def test_streaming_without_capable_provider_is_json_error():
    response = request(build_backend(), "POST", "/v1/chat/completions", body=dict(PAYLOAD, stream=True))
    assert response.status == 400
    assert response.headers["content-type"].startswith("application/json")
    assert json_body(response)["error"]["code"] == "unsupported_capability"
