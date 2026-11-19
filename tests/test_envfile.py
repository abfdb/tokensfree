"""TOKENSFREE_* env files: parsing, precedence and fail-closed errors."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tokensfree.envfile import apply_env_file, parse_env_file

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "e" * 32


def test_parse_ignores_blank_and_comment_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# generated\nTOKENSFREE_LOCAL_TOKEN=%s\n\nTOKENSFREE_ENABLE_REAL=1\n" % TOKEN,
        encoding="utf-8",
    )
    values = parse_env_file(path)
    assert values == {"TOKENSFREE_LOCAL_TOKEN": TOKEN, "TOKENSFREE_ENABLE_REAL": "1"}


def test_parse_tolerates_a_utf8_bom(tmp_path):
    path = tmp_path / ".env"
    path.write_text("\ufeffTOKENSFREE_LOCAL_TOKEN=%s\n" % TOKEN, encoding="utf-8")
    assert parse_env_file(path) == {"TOKENSFREE_LOCAL_TOKEN": TOKEN}


def test_parse_strips_matching_quotes(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        'TOKENSFREE_LOCAL_TOKEN="%s"\n' % TOKEN, encoding="utf-8"
    )
    assert parse_env_file(path) == {"TOKENSFREE_LOCAL_TOKEN": TOKEN}


def test_parse_rejects_foreign_keys_and_bad_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("PATH=/bin\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_env_file(path)
    path.write_text("TOKENSFREE_LOCAL_TOKEN\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_env_file(path)


def test_apply_does_not_override_the_real_environment(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "TOKENSFREE_LOCAL_TOKEN=file-value\nTOKENSFREE_ENABLE_REAL=1\n", encoding="utf-8"
    )
    environ = {"TOKENSFREE_LOCAL_TOKEN": "real-value"}
    applied = apply_env_file(path, environ)
    assert environ["TOKENSFREE_LOCAL_TOKEN"] == "real-value"
    assert environ["TOKENSFREE_ENABLE_REAL"] == "1"
    assert applied == ["TOKENSFREE_ENABLE_REAL"]


def test_host_boots_from_an_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TOKENSFREE_LOCAL_TOKEN=%s\nTOKENSFREE_ENABLE_REAL=0\n" % TOKEN,
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()}
    env.pop("TOKENSFREE_LOCAL_TOKEN", None)
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tokensfree",
            "--env-file",
            str(env_path),
            "--port",
            "0",
            "--db",
            str(tmp_path / "state.db"),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        deadline = time.time() + 20
        announced = None
        while time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if match:
                announced = int(match.group(1))
                break
        assert announced, "gateway did not announce its port from the env file token"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_host_reports_a_bad_env_file(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("PATH=/bin\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree", "--env-file", str(env_path), "--port", "0"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert result.returncode != 0
    assert "cannot read env file" in (result.stdout + result.stderr)
