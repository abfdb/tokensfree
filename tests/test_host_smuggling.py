"""HTTP request parsing: smuggling vectors and hostile framing must be rejected, never crash.

The stdlib host deliberately rejects ANY Transfer-Encoding (chunked included) and
duplicate Content-Length values; these tests pin that behaviour down on a real socket.
"""
import socket

import pytest

from host_support import HostRunner
from tokensfree.config import load_config
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from tokensfree.runtime import build_mock_runtime

TOKEN = "h" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})


@pytest.fixture(scope="module")
def server():
    services = build_mock_runtime(config=CONFIG).services
    backend = MemoryBackend(config=CONFIG, services=services, port=1, token=TOKEN)
    runner = HostRunner(make_asgi_app(backend), max_body=4096)
    port = runner.start()
    backend.port = port  # Host validation needs the real (ephemeral) port
    yield port
    runner.stop()


def exchange(port, payload, *, timeout=10):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(payload)
        sock.settimeout(timeout)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def status_of(response):
    return int(response.split(b" ", 2)[1])


def model_request(port, *, extra_headers=b"", body=b""):
    headers = (
        b"GET /v1/models HTTP/1.1\r\n"
        b"Host: 127.0.0.1:" + str(port).encode() + b"\r\n"
        b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
        + extra_headers
        + b"Connection: close\r\n\r\n"
        + body
    )
    return headers


@pytest.mark.parametrize(
    "encoding",
    [b"chunked", b"identity", b"gzip", b"chunked, gzip"],
)
def test_any_transfer_encoding_is_rejected(server, encoding):
    response = exchange(server, model_request(server, extra_headers=b"Transfer-Encoding: " + encoding + b"\r\n"))
    assert status_of(response) == 400
    assert b"Traceback" not in response


@pytest.mark.parametrize(
    "lengths",
    [b"5, 5", b"5, 6", b"-1", b"abc", b"1e3"],
)
def test_ambiguous_or_invalid_content_length_is_rejected(server, lengths):
    response = exchange(server, model_request(server, extra_headers=b"Content-Length: " + lengths + b"\r\n"))
    assert status_of(response) in (400, 413)
    assert b"Traceback" not in response


def test_oversized_header_block_is_rejected(server):
    response = exchange(
        server,
        model_request(server, extra_headers=b"X-Filler: " + b"a" * 40000 + b"\r\n"),
    )
    assert status_of(response) in (400, 431)
    assert b"Traceback" not in response


def test_body_larger_than_the_limit_is_rejected(server):
    payload = b"x" * 5000
    response = exchange(
        server,
        (
            b"POST /v1/chat/completions HTTP/1.1\r\n"
            b"Host: 127.0.0.1:" + str(server).encode() + b"\r\n"
            b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n"
            + payload
        ),
    )
    assert status_of(response) == 413


def test_bad_request_line_and_version_are_rejected(server):
    for payload in (
        b"GET /v1/models\r\nHost: x\r\n\r\n",
        b"GET /v1/models HTTP/9.9\r\nHost: x\r\n\r\n",
        b"GET v1/models HTTP/1.1\r\nHost: x\r\n\r\n",
    ):
        response = exchange(server, payload)
        assert status_of(response) == 400


def test_query_string_is_ignored(server):
    response = exchange(server, model_request(server).replace(b"GET /v1/models HTTP/1.1", b"GET /v1/models?token=leak HTTP/1.1"))
    assert status_of(response) == 200
    assert b"leak" not in response


def test_connection_closes_after_the_response(server):
    with socket.create_connection(("127.0.0.1", server), timeout=10) as sock:
        sock.sendall(model_request(server))
        sock.settimeout(10)
        data = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    assert b"HTTP/1.1 200" in data
    assert b"connection: close" in data.lower()
