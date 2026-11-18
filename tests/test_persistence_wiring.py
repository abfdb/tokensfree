"""The runnable server actually wires SQLite: idempotency, events, durable replay."""
import asyncio
import json

import pytest

from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import MemoryBackend
from tokensfree.runtime import Storage, build_mock_runtime

TOKEN = "p" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def test_runtime_services_get_storage(storage):
    runtime = build_mock_runtime(config=CONFIG, storage=storage)
    assert runtime.storage is storage
    assert runtime.services.idempotency is storage.idempotency
    assert runtime.services.ledger is storage.ledger
    assert runtime.services.events is storage.events


def test_http_replay_is_served_from_storage(storage):
    runtime = build_mock_runtime(config=CONFIG, storage=storage)
    backend = MemoryBackend(config=CONFIG, services=runtime.services, port=8765, token=TOKEN)
    app = make_asgi_app(backend)

    def post(key):
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [
                (b"host", b"127.0.0.1:8765"),
                (b"authorization", ("Bearer " + TOKEN).encode()),
                (b"content-type", b"application/json"),
                (b"idempotency-key", key.encode()),
            ],
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": json.dumps(PAYLOAD).encode(), "more_body": False}

        async def send(message):
            sent.append(message)

        asyncio.run(app(scope, receive, send))
        return sent[0]["status"], b"".join(m.get("body", b"") for m in sent[1:])

    first_status, first_body = post("key-1")
    second_status, second_body = post("key-1")
    assert first_status == 200 and second_status == 200
    assert first_body == second_body                      # replayed verbatim, same request id
    assert json.loads(first_body)["id"] == json.loads(second_body)["id"]
    events = storage.events.recent(10)
    assert len(events) == 2                               # one attempt + one replay, no ledger charge
    assert {row["outcome"] for row in events} == {"success", "replay"}
    assert json.loads(events[0]["detail"])["status"] == 200

    conflict_status, conflict_body = post("key-1")
    assert conflict_status == 200                         # same key + same payload keeps replaying

    other = b'{"model":"tokensfree-auto","messages":[{"role":"user","content":"different"}]}'
    scope_headers = [
        (b"host", b"127.0.0.1:8765"),
        (b"authorization", ("Bearer " + TOKEN).encode()),
        (b"content-type", b"application/json"),
        (b"idempotency-key", b"key-1"),
    ]
    sent = []

    async def receive_other():
        return {"type": "http.request", "body": other, "more_body": False}

    async def send_other(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": scope_headers}
    asyncio.run(app(scope, receive_other, send_other))
    assert sent[0]["status"] == 409
    assert json.loads(b"".join(m.get("body", b"") for m in sent[1:]))["error"]["code"] == "idempotency_conflict"


def test_idempotency_survives_a_storage_reopen(tmp_path):
    path = str(tmp_path / "state.db")
    first_storage = Storage.open(path)
    try:
        first_runtime = build_mock_runtime(config=CONFIG, storage=first_storage)
        first = asyncio.run(handle_chat(PAYLOAD, services=first_runtime.services, request_id="req-a", idempotency_key="durable"))
        first_id = first.body["id"]
    finally:
        first_storage.close()

    second_storage = Storage.open(path)
    try:
        second_runtime = build_mock_runtime(config=CONFIG, storage=second_storage)
        second = asyncio.run(handle_chat(PAYLOAD, services=second_runtime.services, request_id="req-b", idempotency_key="durable"))
        assert second.status == 200
        assert second.body["id"] == first_id             # same stored response, not a new call
        events = second_storage.events.recent(10)
        assert len(events) == 2
        assert {row["outcome"] for row in events} == {"success", "replay"}
    finally:
        second_storage.close()


def test_storage_rejects_bad_paths():
    with pytest.raises(ValueError):
        Storage.open("")
    with pytest.raises(ValueError):
        Storage.open(None)
