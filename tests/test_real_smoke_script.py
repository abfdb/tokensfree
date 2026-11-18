"""The real-provider acceptance script: self-test with a local fixture upstream."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSH = Path(r"D:\IFan\dsh-extracted\node_modules\@deepseek-ai\dsh\lib\bin.js")


def script_env(**overrides):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("TOKENSFREE_REAL_") or key == "TOKENSFREE_ENABLE_REAL":
            env.pop(key, None)
    env.update(overrides)
    return env


def run_script(*args, env=None, timeout=180):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "real_smoke.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or dict(os.environ),
    )


def test_help_lists_the_fixture_mode():
    result = run_script("--help")
    assert result.returncode == 0
    assert "--fixture" in result.stdout


def test_real_mode_without_environment_fails_closed():
    result = run_script(env=script_env())
    assert result.returncode == 2
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is False
    assert payload["reason"] == "missing_env"
    assert "TOKENSFREE_REAL_BASE_URL" in payload["missing"]
    assert "TOKENSFREE_REAL_API_KEY" in payload["missing"]


@pytest.mark.skipif(os.environ.get("TOKENSFREE_DSH_SMOKE") != "1", reason="set TOKENSFREE_DSH_SMOKE=1 to drive the real DSH client")
def test_fixture_mode_end_to_end():
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    dsh_bin = os.environ.get("TOKENSFREE_DSH_BIN")
    if dsh_bin:
        if not Path(dsh_bin).exists():
            pytest.skip("TOKENSFREE_DSH_BIN does not exist")
    elif not DEFAULT_DSH.exists():
        pytest.skip("unpacked DSH install not found")
    env = dict(os.environ)
    if not env.get("TOKENSFREE_DSH_BIN") and DEFAULT_DSH.exists():
        env["TOKENSFREE_DSH_BIN"] = str(DEFAULT_DSH)
    result = run_script("--fixture", env=env)
    assert result.returncode == 0, (result.stdout[-2000:] + result.stderr[-2000:])
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is True
    assert payload["checks"]["real_mode_reported"] is True
    assert payload["checks"]["dsh_exit_zero"] is True
    assert payload["checks"]["upstream_was_called"] is True
