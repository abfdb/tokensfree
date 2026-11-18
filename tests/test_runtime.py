"""Runtime assembly: config + adapters -> Services, with a real end-to-end call path."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from providers import ProviderSpec
from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import Runtime, build_loopback_runtime, build_mock_runtime, build_services

PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hello"}]}


class Handler(BaseHTTPRequestHandler):
    status = 200
    payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "from fixture"}, "finish_reason": "stop"}]}

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


def call(runtime, payload=None):
    return asyncio.run(handle_chat(PAYLOAD if payload is None else payload, services=runtime.services, request_id="req-e2e"))


def test_loopback_runtime_end_to_end(upstream):
    Handler.status = 200
    Handler.payload = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "from fixture"}, "finish_reason": "stop"}]}
    outcome = call(build_loopback_runtime(upstream))
    assert outcome.status == 200
    assert outcome.provider == "loopback"
    assert outcome.body["choices"][0]["message"]["content"] == "from fixture"


def test_loopback_runtime_surfaces_rate_limit(upstream):
    Handler.status = 429
    try:
        outcome = call(build_loopback_runtime(upstream))
    finally:
        Handler.status = 200
    assert outcome.status == 429
    assert outcome.body["error"]["code"] == "upstream_rate_limited"


def test_mock_runtime_serves_without_sockets():
    runtime = build_mock_runtime()
    outcome = call(runtime)
    assert outcome.status == 200
    assert outcome.provider == "mock-alpha"


def test_evidence_expiry_fails_closed():
    clock = {"now": 100.0}
    runtime = build_mock_runtime(clock=lambda: clock["now"], ttl_s=60.0)
    assert call(runtime).status == 200
    clock["now"] = 1000.0  # evidence TTL was 60s
    outcome = call(runtime)
    assert outcome.status == 503
    assert outcome.body["error"]["code"] == "no_free_provider"


def test_build_services_rejects_inconsistent_wiring():
    config = load_config({})
    spec = ProviderSpec("alpha", "m", "mock_zero_cost", 200)
    with pytest.raises(ValueError):
        build_services(config=config, specs=(spec,), adapters={"ghost": object()}, authorized=frozenset({"alpha"}))
    with pytest.raises(ValueError):
        build_services(config=config, specs=(spec,), adapters={}, authorized=frozenset({"ghost"}))
    with pytest.raises(TypeError):
        build_services(config=config, specs=(spec,), adapters={}, authorized=frozenset({"alpha"}), clock=None)


def test_runtime_dataclass_is_frozen():
    runtime = build_mock_runtime()
    assert isinstance(runtime, Runtime)
    with pytest.raises(Exception):
        runtime.config = load_config({})



def test_fixture_runtime_outlives_a_long_demo():
    # A default fixture runtime is built once and serves for hours; it must not
    # silently turn into 503 after the old 60s fixture TTL.
    clock = {"now": 100.0}
    runtime = build_mock_runtime(clock=lambda: clock["now"])
    assert call(runtime).status == 200
    clock["now"] = 100.0 + 7 * 24 * 3600
    assert call(runtime).status == 200
