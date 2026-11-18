"""Real keys become auditable accounts; successful requests write ledger rows."""
import asyncio
import hashlib

import pytest

from test_multi_key import CONFIG_ON, KEY_ONE, KEY_TWO, MODEL, PROVIDER, KeyAwareUpstream, env_for
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import Storage, build_real_runtime

PAYLOAD = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture()
def storage():
    opened = Storage.open(":memory:")
    try:
        yield opened
    finally:
        opened.close()


@pytest.fixture()
def upstream():
    server = KeyAwareUpstream()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def runtime_for(upstream_port, storage):
    return build_real_runtime(config=CONFIG_ON, env=env_for(upstream_port, KEY_ONE + "," + KEY_TWO), clock=lambda: 1000.0, storage=storage)


def accounts_rows(storage):
    rows = storage.connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
    return [dict(row) for row in rows]


def test_each_key_becomes_a_hashed_account(upstream, storage):
    runtime = runtime_for(upstream.port, storage)
    rows = accounts_rows(storage)
    assert [row["provider"] for row in rows] == [PROVIDER, PROVIDER + "-2"]
    fingerprints = {row["fingerprint"] for row in rows}
    assert fingerprints == {
        hashlib.sha256(KEY_ONE.encode()).hexdigest(),
        hashlib.sha256(KEY_TWO.encode()).hexdigest(),
    }
    assert all(KEY_ONE not in row["credential_ref"] and KEY_TWO not in row["credential_ref"] for row in rows)
    assert all(row["status"] == "active" and row["tier"] == "free_verified" for row in rows)
    assert runtime.services.account_ids[PROVIDER] == rows[0]["id"]


def test_successful_request_writes_a_ledger_row_for_the_serving_account(upstream, storage):
    runtime = runtime_for(upstream.port, storage)
    outcome = asyncio.run(handle_chat(PAYLOAD, services=runtime.services, request_id="req-ledger"))
    assert outcome.status == 200
    assert outcome.provider == PROVIDER + "-2"
    serving_account = runtime.services.account_ids[PROVIDER + "-2"]
    first_account = runtime.services.account_ids[PROVIDER]
    assert storage.ledger.balance(serving_account, unit="request") == -1.0
    assert storage.ledger.balance(first_account, unit="request") == 0.0

    events = storage.events.recent(5)
    assert events[0]["outcome"] == "success"
    assert events[0]["account_id"] == serving_account


def test_replay_records_an_event_but_not_a_second_ledger_charge(upstream, storage):
    runtime = runtime_for(upstream.port, storage)
    first = asyncio.run(handle_chat(PAYLOAD, services=runtime.services, request_id="req-1", idempotency_key="acct-key"))
    second = asyncio.run(handle_chat(PAYLOAD, services=runtime.services, request_id="req-2", idempotency_key="acct-key"))
    assert first.body["id"] == second.body["id"]
    serving_account = runtime.services.account_ids[PROVIDER + "-2"]
    assert storage.ledger.balance(serving_account, unit="request") == -1.0
    outcomes = [row["outcome"] for row in storage.events.recent(5)]
    assert outcomes.count("replay") == 1
    replay_event = next(row for row in storage.events.recent(5) if row["outcome"] == "replay")
    assert replay_event["account_id"] == serving_account


def test_accounts_are_not_duplicated_on_restart(upstream, storage):
    first = runtime_for(upstream.port, storage)
    first_ids = dict(first.services.account_ids)
    assert len(accounts_rows(storage)) == 2
    second = runtime_for(upstream.port, storage)
    assert dict(second.services.account_ids) == first_ids
    assert len(accounts_rows(storage)) == 2
    assert second.storage.health is not None
