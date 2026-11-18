"""Hostile bytes at the HTTP host: always 400/413 or a closed connection, never a hang or crash."""
import http.client
import json
import random
import socket

import pytest

from host_support import HostRunner
from tokensfree.config import load_config
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from tokensfree.runtime import build_mock_runtime

TOKEN = "h" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
HEAD = "POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer " + TOKEN + "\r\nContent-Type: application/json\r\n"


@pytest.fixture()
def host():
    runtime = build_mock_runtime(config=CONFIG)
    backend = MemoryBackend(config=CONFIG, services=runtime.services, port=8765, token=TOKEN)
    runner = HostRunner(make_asgi_app(backend))
    port = runner.start()
    backend.port = port
    try:
        yield port
    finally:
        runner.stop()


def raw(port, payload, timeout=5.0):
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        try:
            return sock.recv(65536)
        except (ConnectionResetError, socket.timeout):
            return b""


def assert_rejected(port, payload):
    data = raw(port, payload)
    assert data == b"" or data.startswith(b"HTTP/1.1 4"), data[:80]
    return data


def test_malformed_requests_are_rejected_but_server_survives(host):
    rng = random.Random(424242)
    cases = [
        b"",
        b"\r\n",
        b"GARBAGE\r\n\r\n",
        b"POST /v1/models HTTP/1.1\r\n",
        b"GET /v1/models HTTP/9.9\r\nHost: x\r\n\r\n",
        b"GET v1/models HTTP/1.1\r\n\r\n",
        b"GET /v1/models HTTP/1.1\r\nBadHeader\r\n\r\n",
        b"GET /v1/models HTTP/1.1\r\nContent-Length: -1\r\n\r\n",
        b"POST /v1/models HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        b"POST /v1/models HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n",
        b"GET /v1/models HTTP/1.1\r\nX-Huge: " + b"a" * 40000 + b"\r\n\r\n",
        b"GET /" + b"a" * 40000 + b" HTTP/1.1\r\n\r\n",
        b"\x00\x01\x02\xff\xfe",
        b"POST /v1/chat/completions HTTP/1.1\r\nHost: 127.0.0.1:1\r\nContent-Length: 1\r\n\r\n{",
    ]
    for case in cases:
        assert_rejected(host, case)
    for _ in range(30):
        junk = bytes(rng.randrange(256) for _ in range(rng.randint(1, 200)))
        data = raw(host, junk)
        assert data == b"" or data.startswith(b"HTTP/1.1 "), data[:80]

    # The server still answers a well-formed request afterwards.
    connection = http.client.HTTPConnection("127.0.0.1", host, timeout=10)
    connection.request("GET", "/v1/models", headers={"Host": "127.0.0.1:" + str(host), "Authorization": "Bearer " + TOKEN})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    assert response.status == 200
    assert json.loads(body)["data"]
