"""Account pool: stored accounts + scheduling + ledger, end to end on SQLite."""
import pytest

from tokensfree.router.account_pool import AccountPool
from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import AccountsRepo, LedgerRepo


@pytest.fixture()
def store():
    connection = connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def make_pool(store, **kwargs):
    accounts = AccountsRepo(store)
    ledger = LedgerRepo(store)
    return AccountPool(accounts, ledger, **kwargs), accounts, ledger


def add(accounts, alias, *, status="active", provider="zhipu"):
    return accounts.add(provider, alias, "keyring://" + provider + "/" + alias, "fp-" + alias, now=1, status=status)


def test_pick_touches_last_used_and_writes_ledger(store):
    pool, accounts, ledger = make_pool(store)
    account_id = add(accounts, "main")
    picked = pool.pick("zhipu", now=100, request_id="req-1")
    assert picked is not None and picked.id == account_id
    assert accounts.get(account_id)["last_used_at"] == 100
    assert ledger.balance(account_id, unit="request") == -1.0


def test_round_robin_across_accounts(store):
    pool, accounts, _ = make_pool(store)
    first = add(accounts, "a")
    second = add(accounts, "b")
    assert pool.pick("zhipu", now=100).id == first
    assert pool.pick("zhipu", now=101).id == second


def test_cooldown_hides_then_restores_an_account(store):
    pool, accounts, _ = make_pool(store)
    first = add(accounts, "a")
    second = add(accounts, "b")
    pool.cool_down(first, seconds=30, now=100)
    assert pool.pick("zhipu", now=101).id == second
    assert pool.pick("zhipu", now=131).id == first


def test_platform_signal_marks_exhausted_and_stops_picking(store):
    pool, accounts, ledger = make_pool(store)
    account_id = add(accounts, "main")
    pool.exhaust(account_id, provider="zhipu", request_id="req-2", now=100)
    assert accounts.get(account_id)["status"] == "exhausted"
    assert pool.pick("zhipu", now=101) is None
    assert ledger.balance(account_id, unit="request") == 0.0


def test_invalidate_stops_picking(store):
    pool, accounts, _ = make_pool(store)
    account_id = add(accounts, "main")
    pool.invalidate(account_id)
    assert accounts.get(account_id)["status"] == "invalid"
    assert pool.pick("zhipu", now=101) is None


def test_no_accounts_returns_none(store):
    pool, _, _ = make_pool(store)
    assert pool.pick("zhipu", now=100) is None


def test_concurrency_cap_is_respected(store):
    pool, accounts, _ = make_pool(store)
    first = add(accounts, "a")
    second = add(accounts, "b")
    assert pool.pick("zhipu", now=100, in_flight={first: 1}, max_concurrency=1).id == second
