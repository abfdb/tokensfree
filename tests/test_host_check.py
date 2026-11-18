"""python -m tokensfree --check validates without binding a port."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = "glm-4.7-flash"


def host_env(token="c" * 32, **overrides):
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = token
    env.update(overrides)
    return env


def run_check(*args, env=None, timeout=60):
    return subprocess.run(
        [sys.executable, "-m", "tokensfree", "--check", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or host_env(),
    )


def test_check_reports_mock_runtime_and_exits(tmp_path):
    started = time.time()
    result = run_check("--db", str(tmp_path / "check.db"))
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert "mock runtime" in payload["mode"]
    assert payload["real_enabled"] is False
    assert [provider["name"] for provider in payload["providers"]] == ["mock-alpha", "mock-beta"]
    assert time.time() - started < 30


def test_check_fails_closed_without_real_env():
    result = run_check("--real")
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "missing real-provider environment" in payload["error"]


def test_check_accepts_a_complete_real_configuration(tmp_path):
    key_file = tmp_path / "zhipu.key"
    key_file.write_text("check-key-0123456789\n", encoding="utf-8")
    snapshot = tmp_path / "evidence.json"
    snapshot.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": "zhipu",
                "model": MODEL,
                "source": "https://example.invalid/pricing.md",
                "sha256": "d" * 64,
                "evidence_expires_at": time.time() + 86400,
            }
        ),
        encoding="utf-8",
    )
    env = host_env(
        TOKENSFREE_ENABLE_REAL="1",
        TOKENSFREE_REAL_PROVIDER="zhipu",
        TOKENSFREE_REAL_BASE_URL="https://example.invalid/v1",
        TOKENSFREE_REAL_MODEL=MODEL,
        TOKENSFREE_REAL_API_KEY_FILE=str(key_file),
        TOKENSFREE_REAL_EVIDENCE_SNAPSHOT=str(snapshot),
    )
    result = run_check("--real", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["real_enabled"] is True
    assert payload["providers"] == [
        {
            "name": "zhipu",
            "model": MODEL,
            "simulation": False,
            "evidence_expires_at": json.loads(snapshot.read_text())["evidence_expires_at"],
        }
    ]


def test_check_does_not_bind_the_port(tmp_path):
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    result = run_check("--port", str(port), "--db", str(tmp_path / "check2.db"))
    assert result.returncode == 0
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", port))
        assert True
    finally:
        listener.close()
