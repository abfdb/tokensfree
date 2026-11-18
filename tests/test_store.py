"""SQLite persistence: migrations, idempotency, quota ledger, accounts, events."""
import sqlite3

import pytest

from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import AccountsRepo, EventsRepo, IdempotencyRepo, LedgerRepo


@pytest.fixture()
def conn():
    connection = connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def test_migration_is_idempotent(conn):
    assert migrate(conn) == 1
    assert migrate(conn) == 1
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "quota_ledger", "idempotency_keys", "events", "schema_version"} <= tables


def test_migration_refuses_future_version(conn):
    conn.execute("INSERT INTO schema_version(version, applied_at) VALUES (99, 0)")
    conn.commit()
    with pytest.raises(ValueError):
        migrate(conn)


def test_idempotency_lifecycle(conn):
    repo = IdempotencyRepo(conn, capacity=10)
    assert repo.reserve("k1", "hash-a", now=100).status == "new"
    assert repo.reserve("k1", "hash-a", now=101).status == "in_flight"
    assert repo.reserve("k1", "hash-b", now=101).status == "conflict"
    repo.complete("k1", '{"ok": true}')
    replay = repo.reserve("k1", "hash-a", now=102)
    assert replay.status == "replay"
    assert replay.response_json == '{"ok": true}'


def test_idempotency_expiry_allows_reuse(conn):
    repo = IdempotencyRepo(conn, capacity=10)
    repo.reserve("k1", "hash-a", now=100, ttl_s=5)
    assert repo.reserve("k1", "hash-a", now=106).status == "new"


def test_idempotency_capacity_is_bounded(conn):
    repo = IdempotencyRepo(conn, capacity=2)
    assert repo.reserve("a", "h", now=1).status == "new"
    assert repo.reserve("b", "h", now=1).status == "new"
    assert repo.reserve("c", "h", now=1).status == "capacity"


def test_accounts_unique_and_status(conn):
    accounts = AccountsRepo(conn)
    account_id = accounts.add("zhipu", "main", "keyring://zhipu/main", "fp-1", now=1)
    with pytest.raises(sqlite3.IntegrityError):
        accounts.add("zhipu", "main", "keyring://zhipu/main-2", "fp-2", now=2)
    accounts.set_status(account_id, "active")
    assert accounts.get(account_id)["status"] == "active"


def test_ledger_is_append_only_and_sums(conn):
    accounts = AccountsRepo(conn)
    account_id = accounts.add("zhipu", "main", "keyring://zhipu/main", "fp", now=1)
    ledger = LedgerRepo(conn)
    ledger.append(account_id, "zhipu", "req-1", "upstream_ok", -3.0, "request", "exact", now=2)
    ledger.append(account_id, "zhipu", "req-2", "upstream_ok", -2.0, "request", "exact", now=3)
    assert ledger.balance(account_id, unit="request") == -5.0
    with pytest.raises(AttributeError):
        ledger.delete  # no deletion API exists


def test_only_platform_signal_marks_exhausted(conn):
    accounts = AccountsRepo(conn)
    account_id = accounts.add("zhipu", "main", "keyring://zhipu/main", "fp", now=1)
    accounts.set_status(account_id, "active")
    ledger = LedgerRepo(conn)
    ledger.append(account_id, "zhipu", "req-1", "upstream_429", -1.0, "request", "estimated", now=2)
    assert accounts.get(account_id)["status"] == "active"
    ledger.platform_signal(account_id, provider="zhipu", request_id="req-2", detail='{"source":"429"}', now=3)
    assert accounts.get(account_id)["status"] == "exhausted"


def test_events_recent_and_retention(conn):
    events = EventsRepo(conn)
    for index in range(5):
        events.append("req-" + str(index), outcome="success", at=1000 + index)
    assert [row["request_id"] for row in events.recent(3)] == ["req-4", "req-3", "req-2"]
    assert events.purge_older_than(days=0, now=1003) == 3
    assert len(events.recent(10)) == 2
