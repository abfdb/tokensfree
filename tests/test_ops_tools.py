"""Ops surface: account summary in /api/status and the accounts admin CLI."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

from providers import ProviderSpec
from tokensfree.adapters.base import ChatResult
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.admission import admit
from tokensfree.runtime import Storage

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "o" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
SPEC = ProviderSpec("alpha", "m-a", "mock_zero_cost", 10 ** 9)


class OkAdapter:
    capabilities = frozenset({"text"})

    async def complete(self, request, admission):
        return ChatResult(content="ok")


@pytest.fixture()
def storage(tmp_path):
    opened = Storage.open(str(tmp_path / "state.db"))
    try:
        yield opened
    finally:
        opened.close()


def services_for(storage):
    account_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    names = frozenset({"alpha"})
    return Services(
        specs=(SPEC,),
        adapters={"alpha": OkAdapter()},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
        ledger=storage.ledger,
        events=storage.events,
        accounts=storage.accounts,
        account_ids={"alpha": account_id},
    )


def status_of(services, port=8765):
    backend = MemoryBackend(config=CONFIG, services=services, port=port, token=TOKEN)
    response = asyncio.run(
        backend.handle(
            EdgeRequest(
                method="GET",
                path="/api/status",
                headers={"host": "127.0.0.1:" + str(port), "authorization": "Bearer " + TOKEN},
            )
        )
    )
    return json.loads(response.body)


def test_status_lists_accounts_with_charged_requests(storage):
    services = services_for(storage)
    payload = status_of(services)
    assert payload["accounts"] == [
        {"provider": "alpha", "status": "active", "cooldown_until": None, "requests_charged": 0}
    ]
    storage.ledger.append(services.account_ids["alpha"], "alpha", "req-1", "request_attempt", -1.0, "request", "estimated", now=100.0)
    payload = status_of(services)
    assert payload["accounts"][0]["requests_charged"] == 1
    assert TOKEN not in json.dumps(payload)


def run_admin(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "accounts_admin.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def test_admin_cli_lists_and_repairs_accounts(storage, tmp_path):
    db_path = str(tmp_path / "state.db")
    account_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100, status="exhausted")
    storage.accounts.set_cooldown(account_id, 99999.0)

    listed = run_admin("--db", db_path)
    assert listed.returncode == 0
    data = json.loads(listed.stdout)
    assert data[0]["provider"] == "alpha"
    assert data[0]["status"] == "exhausted"
    assert data[0]["cooldown_until"] == 99999.0
    assert "fp-a" not in listed.stdout or data[0]["fingerprint"].startswith("fp-a")

    activated = run_admin("--db", db_path, "activate", "alpha")
    assert activated.returncode == 0
    assert storage.accounts.get(account_id)["status"] == "active"
    assert storage.accounts.get(account_id)["cooldown_until"] is None

    missing = run_admin("--db", db_path, "activate", "nope")
    assert missing.returncode == 3
    assert json.loads(missing.stdout)["error"] == "provider_not_found"


def test_admin_cli_clears_cooldowns(storage, tmp_path):
    db_path = str(tmp_path / "state.db")
    account_id = storage.accounts.ensure("alpha", "env-key", "env:alpha", "fp-a", now=100)
    storage.accounts.set_cooldown(account_id, 50000.0)
    result = run_admin("--db", db_path, "reset-cooldown")
    assert result.returncode == 0
    assert json.loads(result.stdout)["cooldowns_cleared"] == 1
    assert storage.accounts.get(account_id)["cooldown_until"] is None


def test_admin_cli_rejects_a_missing_db(tmp_path):
    result = run_admin("--db", str(tmp_path / "missing.db"))
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "db_not_found"
