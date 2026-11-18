"""Exhausted / invalid / cooling accounts are skipped at admission, not retried."""
import asyncio
import json
import time

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services, handle_chat
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.admission import AdmissionDenied, admit
from tokensfree.runtime import Storage

CONFIG = load_config({})
ALPHA = ProviderSpec("alpha", "m-a", "mock_zero_cost", 10 ** 9)
BETA = ProviderSpec("beta", "m-b", "mock_zero_cost", 10 ** 9)
PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}


class RecordingAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, content="ok"):
        self.calls = 0
        self.content = content

    async def complete(self, request, admission):
        self.calls += 1
        return ChatResult(content=self.content)


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def services_with(storage):
    alpha_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    beta_id = storage.accounts.ensure("beta", "env-key", "env:beta", "fp-b", now=100)
    names = frozenset({"alpha", "beta"})
    adapters = {"alpha": RecordingAdapter("from alpha"), "beta": RecordingAdapter("from beta")}
    account_ids = {"alpha": alpha_id, "beta": beta_id}

    # Mirrors the account-aware gate in runtime.build_services.
    def gate(spec):
        admission = admit(spec, CONFIG, names, 100)
        row = storage.accounts.get(account_ids[spec.name])
        if row is not None:
            if row["status"] != "active":
                raise AdmissionDenied("account_" + str(row["status"]))
            if row["cooldown_until"] is not None and row["cooldown_until"] > time.time():
                raise AdmissionDenied("account_cooldown")
        return admission

    services = Services(
        specs=(ALPHA, BETA),
        adapters=adapters,
        admit=gate,
        authorized=names,
        ledger=storage.ledger,
        events=storage.events,
        accounts=storage.accounts,
        account_ids=account_ids,
    )
    return services, adapters, alpha_id, beta_id


def call(services):
    return asyncio.run(handle_chat(PAYLOAD, services=services, request_id="req-routing"))


def test_exhausted_account_is_skipped_without_calling_the_adapter(storage):
    services, adapters, alpha_id, _ = services_with(storage)
    storage.ledger.platform_signal(alpha_id, provider="alpha", request_id="req-1", now=time.time())
    outcome = call(services)
    assert outcome.status == 200 and outcome.provider == "beta"
    assert adapters["alpha"].calls == 0
    assert outcome.body["tokensfree"]["trace"][0] == ["alpha", "denied:account_exhausted"]


def test_invalid_account_is_skipped(storage):
    services, adapters, alpha_id, _ = services_with(storage)
    storage.accounts.set_status(alpha_id, "invalid")
    outcome = call(services)
    assert outcome.status == 200 and outcome.provider == "beta"
    assert adapters["alpha"].calls == 0
    assert outcome.body["tokensfree"]["trace"][0][1] == "denied:account_invalid"


def test_cooling_account_is_skipped_then_restored(storage):
    services, adapters, alpha_id, _ = services_with(storage)
    storage.accounts.set_cooldown(alpha_id, time.time() + 3600)
    outcome = call(services)
    assert outcome.provider == "beta"
    assert adapters["alpha"].calls == 0
    assert outcome.body["tokensfree"]["trace"][0][1] == "denied:account_cooldown"

    storage.accounts.clear_cooldown(alpha_id)
    outcome = call(services)
    assert outcome.provider == "alpha"
    assert adapters["alpha"].calls == 1


def test_status_reports_the_account_block_reason(storage):
    services, _adapters, alpha_id, _ = services_with(storage)
    storage.ledger.platform_signal(alpha_id, provider="alpha", request_id="req-1", now=time.time())
    backend = MemoryBackend(config=CONFIG, services=services, port=8765, token="t" * 32)
    response = asyncio.run(
        backend.handle(
            EdgeRequest(
                method="GET",
                path="/api/status",
                headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + "t" * 32},
            )
        )
    )
    payload = json.loads(response.body)
    alpha = next(provider for provider in payload["providers"] if provider["name"] == "alpha")
    assert alpha["state"] == "blocked"
    assert alpha["policy_reason"] == "account_exhausted"
    assert alpha["model"] == "m-a"
    assert alpha["simulation"] is True
