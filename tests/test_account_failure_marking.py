"""Account-scoped failures update the account record, not just the response."""
import asyncio
import time

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


class FailingAdapter:
    capabilities = frozenset({"text"})

    def __init__(self, error):
        self.error = error

    async def complete(self, request, admission):
        raise self.error


class OkAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="ok")


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


def services_with(storage, alpha_error):
    alpha_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    beta_id = storage.accounts.ensure("beta", "env-key", "env:beta", "fp-b", now=100)
    names = frozenset({"alpha", "beta"})
    services = Services(
        specs=(ALPHA, BETA),
        adapters={"alpha": FailingAdapter(alpha_error), "beta": OkAdapter()},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        ledger=storage.ledger,
        events=storage.events,
        accounts=storage.accounts,
        account_ids={"alpha": alpha_id, "beta": beta_id},
    )
    return services, alpha_id, beta_id


def call(services):
    return asyncio.run(handle_chat(PAYLOAD, services=services, request_id="req-account"))


def ledger_rows(storage, account_id):
    return [
        dict(row)
        for row in storage.connection.execute(
            "SELECT kind, delta_units FROM quota_ledger WHERE account_id = ? ORDER BY id", (account_id,)
        )
    ]


def test_quota_exhaustion_writes_a_platform_signal_and_marks_exhausted(storage):
    services, alpha_id, beta_id = services_with(storage, NormalizedError("quota_exhausted", retryable=False))
    outcome = call(services)
    assert outcome.status == 200
    assert outcome.provider == "beta"
    assert storage.accounts.get(alpha_id)["status"] == "exhausted"
    assert storage.accounts.get(beta_id)["status"] == "active"
    rows = ledger_rows(storage, alpha_id)
    assert [row["kind"] for row in rows] == ["platform_signal"]
    assert rows[0]["delta_units"] == 0.0
    assert storage.ledger.balance(alpha_id) == 0.0


def test_auth_failure_marks_the_account_invalid(storage):
    services, alpha_id, _beta_id = services_with(storage, NormalizedError("auth_invalid", retryable=False))
    outcome = call(services)
    assert outcome.status == 200 and outcome.provider == "beta"
    assert storage.accounts.get(alpha_id)["status"] == "invalid"


def test_rate_limit_sets_a_cooldown(storage):
    services, alpha_id, _beta_id = services_with(
        storage, NormalizedError("rate_limited", retryable=True, retry_after_s=7)
    )
    before = time.time()
    outcome = call(services)
    assert outcome.status == 200 and outcome.provider == "beta"
    cooldown_until = storage.accounts.get(alpha_id)["cooldown_until"]
    assert cooldown_until >= before + 6
    assert storage.accounts.get(alpha_id)["status"] == "active"
