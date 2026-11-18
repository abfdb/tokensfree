"""--db path handling: parent directories are created, bad targets fail clearly."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tokensfree.runtime import Storage

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def opened(tmp_path):
    created = []

    def open_db(path):
        storage = Storage.open(path)
        created.append(storage)
        return storage

    yield open_db
    for storage in created:
        storage.close()


def test_nested_path_creates_parent_directories(opened, tmp_path):
    db_path = tmp_path / "data" / "nested" / "state.db"
    storage = opened(str(db_path))
    storage.events.append("req-1", outcome="success", latency_ms=1, detail="{}", at=100.0)
    assert db_path.exists()
    assert storage.events.recent(1)[0]["request_id"] == "req-1"


def test_tilde_is_expanded(opened, tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    storage = opened("~/tokensfree-test.db")
    storage.events.append("req-2", outcome="success", latency_ms=1, detail="{}", at=100.0)
    assert storage.events.recent(1)[0]["request_id"] == "req-2"
    assert (tmp_path / "tokensfree-test.db").exists()


def test_directory_target_fails():
    with pytest.raises(Exception):
        Storage.open(str(ROOT))


def _host_env():
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = "d" * 32
    return env


def test_host_reports_db_errors_cleanly(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree", "--port", "0", "--db", str(ROOT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=_host_env(),
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "cannot open db" in output
    assert "Traceback" not in output
