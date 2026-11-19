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


def test_probe_endpoint_lists_models(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path != "/v1/models":
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"data": [{"id": "m1"}, {"id": "m2"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        from tokensfree.desktop.process import probe_endpoint

        models, latency = probe_endpoint("http://127.0.0.1:%d/v1" % server.server_port)
        assert models == ["m1", "m2"]
        assert latency >= 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_probe_endpoint_reports_failures():
    import socket
    from tokensfree.desktop.process import probe_endpoint

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(RuntimeError):
        probe_endpoint("http://127.0.0.1:%d/v1" % port, timeout=2)
    with pytest.raises(RuntimeError):
        probe_endpoint("")
