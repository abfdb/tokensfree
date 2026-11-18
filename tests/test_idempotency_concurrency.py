"""Concurrent retries with the same idempotency key: one executes, the other conflicts."""
import asyncio

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.router.admission import admit
from tokensfree.runtime import Storage

CONFIG = load_config({})
SPEC = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 10 ** 9)
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class SlowAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, delay=0.05):
        self.delay = delay
        self.calls = 0

    async def complete(self, request, admission):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return ChatResult(content="done")


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def services(storage, adapter):
    names = frozenset({"alpha"})
    return Services(
        specs=(SPEC,),
        adapters={"alpha": adapter},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        idempotency=storage.idempotency,
        events=storage.events,
    )


def test_same_key_concurrently_executes_once(storage):
    adapter = SlowAdapter()
    service = services(storage, adapter)

    async def run_both():
        return await asyncio.gather(
            handle_chat(PAYLOAD, services=service, request_id="req-a", idempotency_key="same"),
            handle_chat(PAYLOAD, services=service, request_id="req-b", idempotency_key="same"),
        )

    first, second = asyncio.run(run_both())
    statuses = sorted([first.status, second.status])
    assert statuses == [200, 409]
    conflict = first if first.status == 409 else second
    assert conflict.body["error"]["code"] == "idempotency_conflict"
    assert adapter.calls == 1
    outcomes = [row["outcome"] for row in storage.events.recent(10)]
    assert outcomes.count("success") == 1


def test_different_keys_concurrently_both_execute(storage):
    adapter = SlowAdapter()
    service = services(storage, adapter)

    async def run_both():
        return await asyncio.gather(
            handle_chat(PAYLOAD, services=service, request_id="req-a", idempotency_key="one"),
            handle_chat(PAYLOAD, services=service, request_id="req-b", idempotency_key="two"),
        )

    first, second = asyncio.run(run_both())
    assert first.status == 200 and second.status == 200
    assert adapter.calls == 2
