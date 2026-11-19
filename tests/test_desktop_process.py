"""GatewayController: launch on an ephemeral port, poll status, stop cleanly."""
import socket

import pytest

from tokensfree.desktop.config import DesktopConfig
from tokensfree.desktop.process import GatewayController


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture()
def controller(tmp_path):
    config = DesktopConfig(port=0, token="D" * 32, db_path=str(tmp_path / "state.db"), mode="mock")
    gateway = GatewayController(config)
    try:
        yield gateway
    finally:
        gateway.stop()


def test_start_polls_status_and_stops(controller):
    port = controller.start(timeout=40)
    assert port > 0
    assert controller.running
    assert controller.console_url() == "http://127.0.0.1:%d/" % port

    status = controller.status()
    assert status["simulation"] is True
    assert status["persistence"]["idempotency"] is True
    models = controller.models()
    assert len(models) == 2

    logs = controller.logs()
    assert any("tokensfree ASGI backend" in line for line in logs)
    assert not any("D" * 32 in line for line in logs)  # token never logged

    controller.stop()
    assert not controller.running
    with pytest.raises(RuntimeError):
        controller.status()


def test_starting_twice_is_rejected(controller):
    controller.start(timeout=40)
    with pytest.raises(RuntimeError):
        controller.start(timeout=40)


def test_stop_without_start_is_a_noop(controller):
    controller.stop()
    assert not controller.running
    assert controller.exit_code is None
