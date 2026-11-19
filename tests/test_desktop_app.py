"""Desktop panel: construction, rendering with a fake controller, and --self-check."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(__import__("importlib").util.find_spec("tkinter") is None, reason="tkinter missing")

from tokensfree.desktop import config as desktop_config  # noqa: E402
from tokensfree.desktop.app import DesktopApp  # noqa: E402


class FakeController:
    running = True

    def __init__(self):
        self._logs = ["tokensfree ASGI backend: http://127.0.0.1:9999 [mock]", "second line"]
        self.stopped = False

    def console_url(self):
        return "http://127.0.0.1:9999/"

    def status(self):
        return {
            "simulation": True,
            "providers": [
                {"name": "mock-alpha", "model": "mock-a", "state": "eligible"},
                {"name": "mock-beta", "model": "mock-b", "state": "blocked", "policy_reason": "expired"},
            ],
            "accounts": [{"provider": "mock-alpha", "status": "active", "requests_charged": 3}],
            "breakers": {"mock-alpha": {"state": "closed"}},
        }

    def logs(self, tail=None):
        return self._logs[-tail:] if tail else list(self._logs)

    def stop(self):
        self.stopped = True


def make_app(tmp_path):
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as error:
        pytest.skip("no display for tkinter: %s" % error)
    config = desktop_config.DesktopConfig(port=9999, token="T" * 32, db_path=str(tmp_path / "state.db"))
    app = DesktopApp(root, config=config, controller=FakeController())
    root.withdraw()
    return root, app


def test_window_builds_and_renders_status(tmp_path):
    root, app = make_app(tmp_path)
    try:
        root.update()
        app._refresh_status()
        rows = app.tree.get_children()
        assert len(rows) == 4  # 2 providers + 1 account + 1 breaker
        values = [app.tree.item(row, "values") for row in rows]
        assert ("provider", "mock-alpha", "mock-a") in values
        app._refresh_logs()
        assert "ASGI backend" in app.log_text.get("1.0", "end")
    finally:
        root.destroy()


def test_save_settings_persists_the_form(tmp_path):
    root, app = make_app(tmp_path)
    try:
        app.port_var.set("9998")
        app.mode_var.set("loopback")
        app.loopback_var.set("19000")
        config = app.save_settings()
        assert config is not None
        assert config.port == 9998
        assert config.mode == "loopback"
        assert config.loopback_port == 19000
        loaded = desktop_config.load_config()
        assert loaded.port == 9998
    finally:
        root.destroy()


def test_self_check_starts_a_gateway_and_reports(tmp_path):
    env = dict(os.environ)
    env["TOKENSFREE_DESKTOP_DIR"] = str(tmp_path / "desktop")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree.desktop", "--self-check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["ok"] is True
    assert payload["port"] > 0
    assert payload["console_url"].startswith("http://127.0.0.1:")
    assert payload["providers"] == ["mock-alpha", "mock-beta"]
