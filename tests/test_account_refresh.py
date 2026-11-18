"""Replacing a key reactivates its account; a plain restart does not."""
import asyncio
import hashlib

import pytest

from test_multi_key import CONFIG_ON, MODEL, PROVIDER, env_for
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import Storage, build_real_runtime
from upstream_support import JsonUpstream

OK_BODY = '{"choices":[{"index":0,"message":{"role":"assistant","content":"fresh key works"},"finish_reason":"stop"}],"usage":{"total_tokens":2}}'
OLD_KEY = "old-key-0001"
NEW_KEY = "new-key-0002"
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
    server = JsonUpstream(OK_BODY)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def runtime_with(upstream_port, storage, key):
    return build_real_runtime(config=CONFIG_ON, env=env_for(upstream_port, None, key=key), clock=lambda: 1000.0, storage=storage)


def account_row(storage):
    rows = storage.connection.execute("SELECT * FROM accounts").fetchall()
    assert len(rows) == 1
    return dict(rows[0])


def test_restart_with_the_same_key_keeps_the_exhausted_state(upstream, storage):
    first = runtime_with(upstream.port, storage, OLD_KEY)
    account_id = first.services.account_ids[PROVIDER]
    storage.ledger.platform_signal(account_id, provider=PROVIDER, request_id="req-1", now=1000.0)
    assert account_row(storage)["status"] == "exhausted"

    runtime_with(upstream.port, storage, OLD_KEY)
    assert account_row(storage)["status"] == "exhausted"
    assert account_row(storage)["fingerprint"] == hashlib.sha256(OLD_KEY.encode()).hexdigest()


def test_replacing_the_key_reactivates_the_account(upstream, storage):
    first = runtime_with(upstream.port, storage, OLD_KEY)
    account_id = first.services.account_ids[PROVIDER]
    storage.ledger.platform_signal(account_id, provider=PROVIDER, request_id="req-1", now=1000.0)
    storage.accounts.set_cooldown(account_id, 99999.0)
    assert account_row(storage)["status"] == "exhausted"

    second = runtime_with(upstream.port, storage, NEW_KEY)
    row = account_row(storage)
    assert row["status"] == "active"
    assert row["cooldown_until"] is None
    assert row["fingerprint"] == hashlib.sha256(NEW_KEY.encode()).hexdigest()

    outcome = asyncio.run(handle_chat(PAYLOAD, services=second.services, request_id="req-2"))
    assert outcome.status == 200
    assert outcome.provider == PROVIDER
    assert second.storage is storage
