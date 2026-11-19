"""Installer/uninstaller scripts: create and remove user-level shortcuts (temp dirs only)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None,
    reason="PowerShell on Windows only",
)


def run_script(name, *args, timeout=120):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "install" / name), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


@pytest.fixture()
def dirs(tmp_path):
    layout = {
        "start_menu": tmp_path / "start-menu",
        "desktop": tmp_path / "desktop",
        "startup": tmp_path / "startup",
        "data": tmp_path / "data",
    }
    for path in (layout["start_menu"], layout["desktop"], layout["startup"], layout["data"]):
        path.mkdir()
    return layout


def shortcut_dirs(dirs):
    return [
        dirs["start_menu"] / "tokensfree 控制台.lnk",
        dirs["desktop"] / "tokensfree 控制台.lnk",
        dirs["startup"] / "tokensfree 控制台.lnk",
    ]


def test_install_creates_shortcuts_and_uninstall_removes_them(dirs):
    result = run_script(
        "install.ps1",
        "-RepoRoot", str(ROOT),
        "-StartMenuDir", str(dirs["start_menu"]),
        "-DesktopDir", str(dirs["desktop"]),
        "-StartupDir", str(dirs["startup"]),
        "-DataDir", str(dirs["data"]),
        "-Autostart",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for shortcut in shortcut_dirs(dirs):
        assert shortcut.exists(), shortcut
        assert shortcut.stat().st_size > 0
    assert not any("error" in line.lower() for line in result.stdout.splitlines() if "erroraction" not in line.lower())

    uninstall = run_script(
        "uninstall.ps1",
        "-StartMenuDir", str(dirs["start_menu"]),
        "-DesktopDir", str(dirs["desktop"]),
        "-StartupDir", str(dirs["startup"]),
        "-DataDir", str(dirs["data"]),
    )
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    for shortcut in shortcut_dirs(dirs):
        assert not shortcut.exists(), shortcut


def test_install_without_autostart_skips_the_startup_shortcut(dirs):
    result = run_script(
        "install.ps1",
        "-RepoRoot", str(ROOT),
        "-StartMenuDir", str(dirs["start_menu"]),
        "-DesktopDir", str(dirs["desktop"]),
        "-StartupDir", str(dirs["startup"]),
        "-DataDir", str(dirs["data"]),
        "-NoDesktopShortcut",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (dirs["start_menu"] / "tokensfree 控制台.lnk").exists()
    assert not (dirs["desktop"] / "tokensfree 控制台.lnk").exists()
    assert not (dirs["startup"] / "tokensfree 控制台.lnk").exists()


def test_uninstall_remove_data_and_is_idempotent(dirs):
    (dirs["data"] / "desktop.json").write_text("{}", encoding="utf-8")
    args = [
        "-StartMenuDir", str(dirs["start_menu"]),
        "-DesktopDir", str(dirs["desktop"]),
        "-StartupDir", str(dirs["startup"]),
        "-DataDir", str(dirs["data"]),
        "-RemoveData",
    ]
    first = run_script("uninstall.ps1", *args)
    assert first.returncode == 0, first.stdout + first.stderr
    assert not dirs["data"].exists()
    second = run_script("uninstall.ps1", *args)
    assert second.returncode == 0, second.stdout + second.stderr


def test_install_rejects_a_non_repository_root(tmp_path):
    fake = tmp_path / "not-repo"
    fake.mkdir()
    result = run_script(
        "install.ps1",
        "-RepoRoot", str(fake),
        "-StartMenuDir", str(tmp_path / "sm"),
        "-DesktopDir", str(tmp_path / "ds"),
        "-StartupDir", str(tmp_path / "su"),
        "-DataDir", str(tmp_path / "data"),
    )
    assert result.returncode != 0
    assert "does not look like the tokensfree repository" in (result.stdout + result.stderr)
