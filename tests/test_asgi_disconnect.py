"""A client that vanishes mid-stream must not leave the upstream generator open."""
import asyncio
import json

import pytest

from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import EdgeResponse

TOKEN = "d" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})


class FakeStreamingBackend:
    """Bypasses routing: returns a raw stream and records whether it was closed."""

    def __init__(self, chunks=3, fail=False):
        self.closed = False
        self.exhausted = False
        self.chunks = chunks
        self.config = CONFIG
        self.port = 8765
        self.services = Services(specs=(), adapters={}, admit=lambda spec: None, authorized=frozenset())
        self.fail = fail

    async def handle(self, request):
        owner = self

        async def stream():
            try:
                for index in range(owner.chunks):
                    yield ("data: chunk-%d\n\n" % index).encode()
                    await asyncio.sleep(0)
                owner.exhausted = True
            finally:
                owner.closed = True

        return EdgeResponse(status=200, headers={"content-type": "text/event-stream"}, stream=stream())


def run_app(backend, *, fail_after_body_sends=None):
    app = make_asgi_app(backend)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [(b"host", b"127.0.0.1:8765"), (b"authorization", ("Bearer " + TOKEN).encode())],
    }
    sent = []
    state = {"fail_at": fail_after_body_sends, "bodies": 0}

    async def receive():
        return {"type": "http.request", "body": json.dumps({"model": "tokensfree-auto"}).encode(), "more_body": False}

    async def send(message):
        if message.get("type") == "http.response.body" and state["fail_at"] is not None:
            if state["bodies"] == state["fail_at"]:
                raise ConnectionResetError("client gone")
            state["bodies"] += 1
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_completed_stream_marks_exhausted_and_closes():
    backend = FakeStreamingBackend(chunks=2)
    sent = run_app(backend)
    assert backend.exhausted is True
    assert backend.closed is True
    assert any(message.get("body", b"") == b"" and message.get("more_body") is False for message in sent)


def test_client_disconnect_closes_the_stream():
    backend = FakeStreamingBackend(chunks=10)
    with pytest.raises(ConnectionResetError):
        run_app(backend, fail_after_body_sends=0)
    assert backend.closed is True
    assert backend.exhausted is False


def test_client_disconnect_after_first_chunk_also_closes():
    backend = FakeStreamingBackend(chunks=10)
    with pytest.raises(ConnectionResetError):
        run_app(backend, fail_after_body_sends=1)
    assert backend.closed is True
