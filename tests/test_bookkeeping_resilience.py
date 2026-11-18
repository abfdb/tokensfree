"""A storage hiccup never turns a good answer into a 500."""
import asyncio

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult, NormalizedError
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.router.admission import admit
from tokensfree.runtime import Storage

CONFIG = load_config({})
ALPHA = ProviderSpec("alpha", "m-a", "mock_zero_cost", 10 ** 9)
BETA = ProviderSpec("beta", "m-b", "mock_zero_cost", 10 ** 9)
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class OkAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="ok")


class FailingAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        raise NormalizedError("auth_invalid", retryable=False)


def explode(*args, **kwargs):
    raise RuntimeError("storage went away")


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def services_with(storage, *, specs=(ALPHA,), adapters=None, **extra):
    names = frozenset(spec.name for spec in specs)
    adapters = adapters or {spec.name: OkAdapter() for spec in specs}
    return Services(
        specs=tuple(specs),
        adapters=adapters,
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        idempotency=extra.pop("idempotency", storage.idempotency),
        ledger=extra.pop("ledger", storage.ledger),
        events=extra.pop("events", storage.events),
        accounts=extra.pop("accounts", storage.accounts),
        account_ids=extra.pop("account_ids", None),
        **extra,
    )


def test_ledger_and_event_failures_do_not_break_the_response(storage):
    storage.ledger.append = explode
    storage.events.append = explode
    outcome = asyncio.run(handle_chat(PAYLOAD, services=services_with(storage), request_id="req-1"))
    assert outcome.status == 200
    assert outcome.body["choices"][0]["message"]["content"] == "ok"


def test_idempotency_complete_failure_keeps_the_response(storage):
    storage.idempotency.complete = explode
    outcome = asyncio.run(
        handle_chat(PAYLOAD, services=services_with(storage), request_id="req-2", idempotency_key="k-complete")
    )
    assert outcome.status == 200
    assert outcome.body["choices"][0]["message"]["content"] == "ok"


def test_replay_event_failure_still_replays(storage):
    first = asyncio.run(
        handle_chat(PAYLOAD, services=services_with(storage), request_id="req-3", idempotency_key="k-replay")
    )
    storage.events.append = explode
    second = asyncio.run(
        handle_chat(PAYLOAD, services=services_with(storage), request_id="req-4", idempotency_key="k-replay")
    )
    assert second.status == first.status == 200
    assert second.body["id"] == first.body["id"]


def test_account_bookkeeping_failure_does_not_break_fallback(storage):
    account_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    storage.accounts.set_status = explode
    services = services_with(
        storage,
        specs=(ALPHA, BETA),
        adapters={"alpha": FailingAdapter(), "beta": OkAdapter()},
        account_ids={"alpha": account_id},
    )
    outcome = asyncio.run(handle_chat(PAYLOAD, services=services, request_id="req-5"))
    assert outcome.status == 200
    assert outcome.provider == "beta"
