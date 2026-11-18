"""Minimal HTTP host: real socket round-trips against the ASGI app."""
import http.client
import json
import socket
import threading

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from host_support import HostRunner
from tokensfree.router.admission import admit

TOKEN = "d" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PLAIN = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 200)
STREAMING = ProviderSpec("stream-alpha", "mock-stream", "mock_zero_cost", 200, frozenset({"text", "stream"}))
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class FakeAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="hello host")


class FakeStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        yield StreamEvent("delta", {"role": "assistant"})
        yield StreamEvent("delta", {"content": "host sse"})
        yield StreamEvent("finish", finish_reason="stop")


def make_app(specs, adapters, max_body=262144):
    names = frozenset(spec.name for spec in specs)
    services = Services(specs=tuple(specs), adapters=adapters, admit=lambda spec: admit(spec, CONFIG, names, 100), authorized=names)
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)
    return backend, make_asgi_app(backend)


def request(port, method, path, *, body=None, headers=None):
    merged = {"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + TOKEN}
    merged.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body, merged)
    response = connection.getresponse()
    payload = response.read()
    result = (response.status, {name.lower(): value for name, value in response.getheaders()}, payload)
    connection.close()
    return result


def test_models_over_real_socket():
    backend, app = make_app((PLAIN,), {PLAIN.name: FakeAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    try:
        status, headers, body = request(port, "GET", "/v1/models")
        assert status == 200
        assert headers["connection"].lower() == "close"
        assert json.loads(body)["data"][0]["id"] == "mock-a"
    finally:
        runner.stop()


def test_chat_over_real_socket():
    backend, app = make_app((PLAIN,), {PLAIN.name: FakeAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    try:
        status, headers, body = request(port, "POST", "/v1/chat/completions", body=json.dumps(PAYLOAD).encode())
        assert status == 200
        assert json.loads(body)["choices"][0]["message"]["content"] == "hello host"
        assert int(headers["content-length"]) == len(body)
    finally:
        runner.stop()


def test_streaming_over_real_socket_uses_chunked():
    backend, app = make_app((STREAMING,), {STREAMING.name: FakeStreamAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    try:
        status, headers, body = request(port, "POST", "/v1/chat/completions",
                                        body=json.dumps(dict(PAYLOAD, stream=True)).encode())
        assert status == 200
        assert headers.get("transfer-encoding", "").lower() == "chunked"
        text = body.decode("utf-8")
        assert text.strip().endswith("data: [DONE]")
        assert "host sse" in text
    finally:
        runner.stop()


def test_bad_host_rejected_over_socket():
    backend, app = make_app((PLAIN,), {PLAIN.name: FakeAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    try:
        status, _, body = request(port, "GET", "/v1/models", headers={"Host": "evil.example"})
        assert status == 403
        assert json.loads(body)["error"]["code"] == "origin_rejected"
    finally:
        runner.stop()


def test_oversized_body_is_413():
    backend, app = make_app((PLAIN,), {PLAIN.name: FakeAdapter()})
    runner = HostRunner(app, max_body=1024)
    port = runner.start()
    backend.port = port
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("POST", "/v1/chat/completions", b"x" * 2048,
                               {"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + TOKEN})
        except (BrokenPipeError, ConnectionResetError):
            pass
        response = connection.getresponse()
        assert response.status == 413
        connection.close()
    finally:
        runner.stop()


def test_malformed_request_line_is_400():
    backend, app = make_app((PLAIN,), {PLAIN.name: FakeAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=10)
        client.sendall(b"GARBAGE\r\n\r\n")
        data = client.recv(4096)
        client.close()
        assert b" 400 " in data.split(b"\r\n", 1)[0]
    finally:
        runner.stop()


class EchoStreamAdapter:
    capabilities = frozenset({"text", "stream"})

    async def stream(self, request, admission):
        token = request.messages[0]["content"]
        yield StreamEvent("delta", {"role": "assistant"})
        yield StreamEvent("delta", {"content": "tok:" + token})
        yield StreamEvent("finish", finish_reason="stop")


def test_concurrent_streams_do_not_cross_talk():
    backend, app = make_app((STREAMING,), {STREAMING.name: EchoStreamAdapter()})
    runner = HostRunner(app)
    port = runner.start()
    backend.port = port
    results = {}
    try:
        def worker(index):
            payload = {"model": "tokensfree-auto", "stream": True, "messages": [{"role": "user", "content": "req-" + str(index)}]}
            status, _, body = request(port, "POST", "/v1/chat/completions", body=json.dumps(payload).encode())
            results[index] = (status, body.decode("utf-8"))

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        assert sorted(results) == [0, 1, 2, 3]
        for index in range(4):
            status, text = results[index]
            assert status == 200
            assert "tok:req-" + str(index) in text
            for other in range(4):
                if other != index:
                    assert "tok:req-" + str(other) not in text
    finally:
        runner.stop()
