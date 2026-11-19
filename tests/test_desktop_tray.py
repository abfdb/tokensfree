"""Windows system tray: lifecycle only (menu interactions need a real desktop)."""
import pytest

from tokensfree.desktop import tray as tray_module

pytestmark = pytest.mark.skipif(not tray_module.IS_WINDOWS, reason="Windows tray only")


def test_tray_starts_and_stops():
    commands = []
    icon = tray_module.TrayIcon(on_command=commands.append)
    if not icon.start(timeout=8):
        pytest.skip("no shell tray available in this session: %s" % (icon.error,))
    try:
        assert icon.error is None
        assert icon._hwnd
    finally:
        icon.stop()
    assert icon._started is False


def test_unsupported_platform_reports_cleanly(monkeypatch):
    monkeypatch.setattr(tray_module, "IS_WINDOWS", False)
    icon = tray_module.TrayIcon()
    assert icon.start(timeout=1) is False
    assert "only implemented on Windows" in icon.error
