"""Loopback adapter: async wrapper over the offline protocol fixture, 127.0.0.1 only."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatRequest, ChatResult, NormalizedError
from tokensfree.adapters.loopback import LoopbackAdapter
from tokensfree.router.admission import admit

REQUEST = ChatRequest(model="fixture-model", messages=({"role": "user", "content": "hi"},))


def admission():
    spec = ProviderSpec("loopback", "fixture-model", "mock_zero_cost", 200)
    return admit(spec, type("C", (), {"enable_real": False})(), frozenset({"loopback"}), 100)


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}]}

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(type(self).payload).encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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


def test_success_maps_to_chat_result(upstream):
    Handler.status = 200
    Handler.payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}]}
    result = asyncio.run(LoopbackAdapter(upstream).complete(REQUEST, admission()))
    assert isinstance(result, ChatResult)
    assert result.content == "hello"
    assert result.finish_reason == "stop"


def test_rate_limit_maps_to_retryable_error(upstream):
    Handler.status = 429
    Handler.payload = {}
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(LoopbackAdapter(upstream).complete(REQUEST, admission()))
    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True
    Handler.status = 200


def test_adapter_requires_admission(upstream):
    with pytest.raises(TypeError):
        asyncio.run(LoopbackAdapter(upstream).complete(REQUEST, None))


def test_rejects_invalid_port():
    for port in ("80", 0, 65536, True):
        with pytest.raises(ValueError):
            LoopbackAdapter(port)
