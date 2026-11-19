"""Onboarding scripts: UTF-8 BOM, PowerShell syntax and .env generation."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
POWERSHELL = shutil.which("powershell") or shutil.which("powershell.exe")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None,
    reason="PowerShell scripts are Windows-only in this test",
)


def test_scripts_keep_a_utf8_bom_for_windows_powershell():
    # Windows PowerShell 5.1 misreads UTF-8 without BOM, garbling Chinese text.
    for name in ("quickstart.ps1", "start.ps1"):
        raw = (SCRIPTS / name).read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), name + " lost its UTF-8 BOM"


def test_scripts_parse_as_powershell():
    if POWERSHELL is None:
        pytest.skip("powershell not on PATH")
    for name in ("quickstart.ps1", "start.ps1"):
        expression = (
            "$null = [scriptblock]::Create((Get-Content -Raw -LiteralPath '"
            + str(SCRIPTS / name)
            + "')); exit 0"
        )
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-Command", expression],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_quickstart_generates_a_gitignored_env(tmp_path):
    (tmp_path / "scripts").mkdir()
    for name in ("quickstart.ps1", "start.ps1"):
        shutil.copy(SCRIPTS / name, tmp_path / "scripts" / name)
    fake_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_bytes(b"placeholder")

    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "scripts" / "quickstart.ps1"), "-NoStart"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        env=dict(os.environ),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8-sig")
    match = re.search(r"^TOKENSFREE_LOCAL_TOKEN=(\S+)$", env_text, re.MULTILINE)
    assert match, "quickstart did not write a local token"
    token = match.group(1)
    assert 32 <= len(token) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    assert "TOKENSFREE_ENABLE_REAL=0" in env_text


def test_start_script_uses_the_env_file_and_a_local_database():
    text = (SCRIPTS / "start.ps1").read_text(encoding="utf-8-sig")
    assert "--env-file" in text
    assert ".tokensfree" in text
    assert "tokensfree" in text

def test_scripts_are_ignored_from_the_repository():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore
    assert ".tokensfree/" in gitignore
