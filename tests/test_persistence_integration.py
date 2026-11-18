"""Persistence wired into the request path: idempotent replay, ledger and events."""
import asyncio
import dataclasses

import pytest

from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import build_mock_runtime
from tokensfree.store.db import connect, migrate
from tokensfree.store.repos import AccountsRepo, EventsRepo, IdempotencyRepo, LedgerRepo

PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hello"}]}


@pytest.fixture()
def store():
    connection = connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


def services_with(store, runtime, *, idempotency=None, ledger=None, events=None):
    return dataclasses.replace(
        runtime.services,
        idempotency=idempotency,
        ledger=ledger,
        events=events,
    )


def call(services, payload=None, *, key=None, account_id=None, request_id="req-1"):
    return asyncio.run(
        handle_chat(
            PAYLOAD if payload is None else payload,
            services=services,
            request_id=request_id,
            idempotency_key=key,
            account_id=account_id,
        )
    )


def test_replay_returns_cached_body_without_second_call(store):
    runtime = build_mock_runtime()
    repository = IdempotencyRepo(store)
    services = services_with(store, runtime, idempotency=repository)
    adapter = services.adapters["mock-alpha"]

    first = call(services, key="k1")
    second = call(services, key="k1")
    assert first.status == 200 and second.status == 200
    assert first.body == second.body
    assert adapter.calls == 1


def test_same_key_different_payload_conflicts(store):
    runtime = build_mock_runtime()
    services = services_with(store, runtime, idempotency=IdempotencyRepo(store))
    assert call(services, key="k1").status == 200
    other = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "different"}]}
    conflict = call(services, other, key="k1")
    assert conflict.status == 409
    assert conflict.body["error"]["code"] == "idempotency_conflict"


def test_failure_is_cached_too(store):
    clock = {"now": 100.0}
    runtime = build_mock_runtime(clock=lambda: clock["now"])
    services = services_with(store, runtime, idempotency=IdempotencyRepo(store))
    clock["now"] = 10_000.0  # evidence expired
    first = call(services, key="kf")
    second = call(services, key="kf")
    assert first.status == 503
    assert second.status == 503
    assert second.body == first.body


def test_ledger_and_events_are_written(store):
    accounts = AccountsRepo(store)
    account_id = accounts.add("mock", "main", "keyring://mock/main", "fp", now=1)
    runtime = build_mock_runtime()
    services = services_with(store, runtime, ledger=LedgerRepo(store), events=EventsRepo(store))

    outcome = call(services, account_id=account_id, request_id="req-ledger")
    assert outcome.status == 200
    assert LedgerRepo(store).balance(account_id, unit="request") == -1.0
    recent = EventsRepo(store).recent(5)
    assert any(row["request_id"] == "req-ledger" and row["outcome"] == "success" for row in recent)


def test_replay_does_not_write_extra_ledger_rows(store):
    accounts = AccountsRepo(store)
    account_id = accounts.add("mock", "main", "keyring://mock/main", "fp", now=1)
    runtime = build_mock_runtime()
    ledger = LedgerRepo(store)
    services = services_with(store, runtime, idempotency=IdempotencyRepo(store), ledger=ledger)

    call(services, key="k1", account_id=account_id)
    call(services, key="k1", account_id=account_id)
    assert ledger.balance(account_id, unit="request") == -1.0
