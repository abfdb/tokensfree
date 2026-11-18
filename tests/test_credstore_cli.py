"""credential_store.py: set from stdin, status and clear round trip."""
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tokensfree import credstore

pytestmark = pytest.mark.skipif(not credstore.is_supported(), reason="Windows Credential Manager only")

ROOT = Path(__file__).resolve().parents[1]
SECRET = "cli-secret-0123456789"


@pytest.fixture()
def provider():
    name = "tf-cli-" + uuid.uuid4().hex[:12]
    try:
        yield name
    finally:
        try:
            credstore.delete_secret(name)
        except credstore.CredentialError:
            pass


def run_cli(*args, stdin=None):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "credential_store.py"), *args],
        cwd=str(ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_set_status_clear_round_trip(provider):
    saved = run_cli("set", "--provider", provider, "--stdin", stdin=SECRET + "\n")
    assert saved.returncode == 0, saved.stdout + saved.stderr
    payload = json.loads(saved.stdout)
    assert payload["verified"] is True
    assert SECRET not in saved.stdout

    status = run_cli("status", "--provider", provider)
    assert status.returncode == 0
    assert json.loads(status.stdout)["present"] is True
    assert credstore.load_secret(provider) == SECRET

    cleared = run_cli("clear", "--provider", provider)
    assert cleared.returncode == 0
    assert json.loads(cleared.stdout)["deleted"] is True

    status = run_cli("status", "--provider", provider)
    assert json.loads(status.stdout)["present"] is False


def test_empty_secret_is_refused(provider):
    result = run_cli("set", "--provider", provider, "--stdin", stdin="\n")
    assert result.returncode == 2
    assert json.loads(result.stdout)["reason"] == "empty_secret"


def test_invalid_provider_is_refused():
    result = run_cli("status", "--provider", "Bad Provider")
    assert result.returncode == 2
    assert "provider" in json.loads(result.stdout)["detail"]
