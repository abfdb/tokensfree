"""`python -m tokensfree` must expose the same host flags without a console script."""
import os
import subprocess
import sys

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_module_help_lists_the_host_flags():
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    assert "--port" in result.stdout
    assert "--loopback" in result.stdout


def test_module_without_token_fails_closed():
    # Keep the real environment (Windows Python needs SystemRoot/TEMP) but drop the token.
    env = dict(os.environ)
    env.pop("TOKENSFREE_LOCAL_TOKEN", None)
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree", "--port", "0"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    assert result.returncode != 0
    assert "TOKENSFREE_LOCAL_TOKEN" in (result.stdout + result.stderr)
