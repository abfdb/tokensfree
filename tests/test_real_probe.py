"""The direct real-provider probe: fixture self-test and fail-closed env handling."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def probe_env(**overrides):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("TOKENSFREE_REAL_") or key == "TOKENSFREE_ENABLE_REAL":
            env.pop(key, None)
    env.update(overrides)
    return env


def run_probe(*args, env=None, timeout=120):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "real_probe.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or dict(os.environ),
    )


def test_help_mentions_the_flags():
    result = run_probe("--help")
    assert result.returncode == 0
    assert "--fixture" in result.stdout
    assert "--stream" in result.stdout
    assert "--tools" in result.stdout


def test_real_mode_without_environment_fails_closed():
    result = run_probe(env=probe_env())
    assert result.returncode == 2
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is False
    assert "TOKENSFREE_REAL_BASE_URL" in payload["missing"]


def test_fixture_mode_validates_all_three_paths():
    result = run_probe("--fixture", "--stream", "--tools", env=probe_env())
    assert result.returncode == 0, (result.stdout[-2000:] + result.stderr[-2000:])
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is True
    assert payload["checks"] == {"nonstream_ok": True, "stream_ok": True, "tools_ok": True}
    assert payload["details"]["tool_calls"] == ["read"]
    assert "sk-fixture-only" not in (result.stdout + result.stderr)
