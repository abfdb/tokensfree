"""Streaming requests are recorded in the ledger and events, like JSON requests."""
import asyncio

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatRequest, NormalizedError
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.runtime import Storage

CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": "s" * 32})
TOKEN = "s" * 32
REQUEST = ChatRequest(model="m", messages=({"role": "user", "content": "hi"},))


def spec(name="alpha"):
    return ProviderSpec(name, "m", "mock_zero_cost", 10 ** 9, frozenset({"text", "stream"}), simulation=True)


class FakeStreamAdapter:
    def __init__(self, events=(), error=None):
        self.events = list(events)
        self.error = error

    async def stream(self, request, admission):
        for event in self.events:
            yield event
        if self.error is not None:
            raise self.error


OK_EVENTS = (
    StreamEvent("delta", delta={"role": "assistant"}),
    StreamEvent("delta", delta={"content": "OK"}),
    StreamEvent("finish", finish_reason="stop"),
    StreamEvent("done"),
)


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def services_with(storage, candidates):
    names = frozenset(name for name, _adapter in candidates)
    account_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    specs = tuple(spec(name) for name, _adapter in candidates)
    return Services(
        specs=specs,
        adapters={name: adapter for name, adapter in candidates},
        admit=lambda candidate: "admission",
        authorized=names,
        ledger=storage.ledger,
        events=storage.events,
        accounts=storage.accounts,
        account_ids={"alpha": account_id},
    )


def stream_through_backend(services, payload):
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)
    request = EdgeRequest(
        method="POST",
        path="/v1/chat/completions",
        headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN, "content-type": "application/json"},
        body=__import__("json").dumps(payload).encode(),
    )

    async def run():
        response = await backend.handle(request)
        frames = []
        if response.stream is not None:
            async for chunk in response.stream:
                frames.append(chunk)
        return response, b"".join(frames)

    return asyncio.run(run())


def chat_payload(stream=True):
    return {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}], "stream": stream}


def test_successful_stream_records_ledger_and_event(storage):
    services = services_with(storage, [("alpha", FakeStreamAdapter(OK_EVENTS))])
    response, body = stream_through_backend(services, chat_payload())
    assert response.status == 200
    assert body.strip().endswith(b"data: [DONE]")
    account_id = services.account_ids["alpha"]
    assert storage.ledger.balance(account_id, unit="request") == -1.0
    assert storage.events.recent(1)[0]["outcome"] == "success"


def test_stream_fallback_settles_on_the_serving_provider(storage):
    alpha = FakeStreamAdapter(error=NormalizedError("rate_limited", retryable=True))
    beta = FakeStreamAdapter(OK_EVENTS)
    services = services_with(storage, [("alpha", alpha), ("beta", beta)])
    _response, body = stream_through_backend(services, chat_payload())
    assert b'"model": "beta"' in body


def test_stream_error_after_first_frame_settles_with_the_error(storage):
    adapter = FakeStreamAdapter(
        events=(StreamEvent("delta", delta={"role": "assistant"}),),
        error=NormalizedError("refusal", retryable=False),
    )
    services = services_with(storage, [("alpha", adapter)])
    _response, body = stream_through_backend(services, chat_payload())
    assert b'"error"' in body
    assert storage.events.recent(1)[0]["outcome"] == "error"


def test_pre_stream_rejection_is_recorded(storage):
    services = services_with(storage, [("alpha", FakeStreamAdapter(OK_EVENTS))])
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)
    request = EdgeRequest(
        method="POST",
        path="/v1/chat/completions",
        headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN, "content-type": "application/json"},
        body=b'{"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": true, "n": 2}',
    )
    response = asyncio.run(backend.handle(request))
    assert response.status == 400
    events = storage.events.recent(5)
    assert events and events[0]["outcome"] == "error"
