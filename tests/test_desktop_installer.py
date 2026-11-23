"""Installer/uninstaller scripts: create and remove user-level shortcuts (temp dirs only)."""
import shutil
import struct
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


def test_install_preserves_unicode_shortcut_directory_and_working_directory(tmp_path):
    repo = tmp_path / "测试项目"
    (repo / "tokensfree").mkdir(parents=True)
    pythonw = repo / ".venv" / "Scripts" / "pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    shutil.copyfile(sys.executable, pythonw)
    start_menu = tmp_path / "快捷入口"
    args = [
        "-StartMenuDir", str(start_menu),
        "-DesktopDir", str(tmp_path / "desktop"),
        "-StartupDir", str(tmp_path / "startup"),
        "-DataDir", str(tmp_path / "data"),
    ]
    shortcut = start_menu / "tokensfree 控制台.lnk"

    # Re-running the installer should replace, not multiply, its shortcut.
    for _ in range(2):
        result = run_script("install.ps1", "-RepoRoot", str(repo), "-NoDesktopShortcut", *args)
        assert result.returncode == 0, result.stdout + result.stderr
    assert list(start_menu.iterdir()) == [shortcut]

    # Parse the MS-SHLLINK StringData fields without using the ANSI WScript reader.
    data = shortcut.read_bytes()
    header_size, = struct.unpack_from("<I", data)
    assert header_size == 0x4C
    flags, = struct.unpack_from("<I", data, 0x14)
    assert flags & 0x80  # IsUnicode
    offset = header_size
    if flags & 1:  # HasLinkTargetIDList
        size, = struct.unpack_from("<H", data, offset)
        offset += 2 + size
    if flags & 2:  # HasLinkInfo
        size, = struct.unpack_from("<I", data, offset)
        offset += size
    strings = {}
    for bit in range(2, 7):
        if flags & (1 << bit):
            length, = struct.unpack_from("<H", data, offset)
            offset += 2
            end = offset + 2 * length
            strings[bit] = data[offset:end].decode("utf-16-le")
            offset = end
    assert Path(strings[4]) == repo
    assert strings[5] == "-m tokensfree.desktop --start-gateway"

    uninstall = run_script("uninstall.ps1", *args)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert not shortcut.exists()


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
