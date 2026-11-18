"""Full streaming path through the real HTTP host and a real local SSE upstream."""
import http.client
import json

import pytest

from tokensfree.config import load_config
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from host_support import HostRunner
from upstream_support import SSEUpstream
from tokensfree.runtime import build_loopback_streaming_runtime

UPSTREAM_SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"upstream hello"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)

TOKEN = "f" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}

def start_backend(upstream_port):
    runtime = build_loopback_streaming_runtime(upstream_port, config=CONFIG)
    backend = MemoryBackend(config=CONFIG, services=runtime.services, port=8765, token=TOKEN)
    runner = HostRunner(make_asgi_app(backend))
    port = runner.start()
    backend.port = port
    return runner, port


def request(port, body):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(
        "POST",
        "/v1/chat/completions",
        json.dumps(body).encode(),
        {"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    response = connection.getresponse()
    payload = response.read()
    headers = {name.lower(): value for name, value in response.getheaders()}
    status = response.status
    connection.close()
    return status, headers, payload


@pytest.fixture()
def upstream():
    server = SSEUpstream(UPSTREAM_SSE)
    server.start()
    try:
        yield server.port
    finally:
        server.stop()


def test_streaming_end_to_end_through_real_host(upstream):
    runner, port = start_backend(upstream)
    try:
        status, headers, body = request(port, PAYLOAD)
        assert status == 200
        assert headers.get("transfer-encoding", "").lower() == "chunked"
        text = body.decode("utf-8")
        assert "upstream hello" in text
        assert text.strip().endswith("data: [DONE]")
    finally:
        runner.stop()


def test_non_stream_request_to_streaming_only_runtime_is_rejected(upstream):
    runner, port = start_backend(upstream)
    try:
        status, headers, body = request(port, {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]})
        assert status == 400
        assert headers["content-type"].startswith("application/json")
        assert json.loads(body)["error"]["code"] == "unsupported_capability"
    finally:
        runner.stop()
