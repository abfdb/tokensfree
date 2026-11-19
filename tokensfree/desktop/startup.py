"""User-level autostart for the desktop shell (Startup folder, no registry)."""
import os
import pathlib
import sys

LAUNCHER_NAME = "tokensfree-desktop.cmd"


def startup_dir():
    appdata = os.environ.get("APPDATA") or os.environ.get("USERPROFILE") or str(pathlib.Path.home())
    return pathlib.Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def launcher_path():
    return startup_dir() / LAUNCHER_NAME


def pythonw_executable():
    executable = pathlib.Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else str(executable)


def launcher_content(repo_root):
    return (
        "@echo off\r\n"
        "cd /d \"%s\"\r\n"
        "start \"\" \"%s\" -m tokensfree.desktop --start-gateway\r\n" % (repo_root, pythonw_executable())
    )


def set_autostart(enabled, *, repo_root):
    path = launcher_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(launcher_content(repo_root), encoding="utf-8", newline="")
        return path
    return remove_autostart()


def remove_autostart():
    path = launcher_path()
    if path.exists():
        path.unlink()
        return path
    return None


def is_autostart_enabled():
    return launcher_path().exists()
