"""Profile management API: list/save/delete/test behind the local token."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tokensfree.config import load_config
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.runtime import build_mock_runtime

TOKEN = "a" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})


def make_backend(profiles_path):
    services = build_mock_runtime(config=CONFIG).services
    return MemoryBackend(
        config=CONFIG,
        services=services,
        port=8765,
        token=TOKEN,
        profiles_path=profiles_path,
    )


def call(backend, method, path, *, body=None, token=TOKEN):
    headers = {"host": "127.0.0.1:8765", "content-type": "application/json"}
    if token is not None:
        headers["authorization"] = "Bearer " + token
    request = EdgeRequest(
        method=method,
        path=path,
        headers=headers,
        body=json.dumps(body).encode() if body is not None else b"",
    )
    response = asyncio.run(backend.handle(request))
    return response.status, json.loads(response.body) if response.body else {}


LOCAL_PROFILE = {
    "name": "本机 Ollama",
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "qwen2.5:7b",
    "cost_policy": "zero_cost_local",
    "capabilities": ["text", "stream"],
}


def test_requires_the_local_token(tmp_path):
    backend = make_backend(tmp_path / "providers.json")
    status, _payload = call(backend, "GET", "/api/profiles", token=None)
    assert status == 401


def test_disabled_without_a_profiles_path():
    backend = make_backend(None)
    status, payload = call(backend, "GET", "/api/profiles")
    assert status == 200 and payload["enabled"] is False
    status, payload = call(backend, "POST", "/api/profiles", body=LOCAL_PROFILE)
    assert status == 400 and payload["ok"] is False


def test_create_list_delete_round_trip(tmp_path):
    path = tmp_path / "providers.json"
    backend = make_backend(path)
    status, payload = call(backend, "GET", "/api/profiles")
    assert status == 200 and payload["ok"] is True and payload["profiles"] == []

    status, payload = call(backend, "POST", "/api/profiles", body=LOCAL_PROFILE)
    assert status == 200, payload
    assert payload["ok"] is True and payload["restart_required"] is True
    profile = payload["profile"]
    assert profile["name"] == "本机 Ollama" and profile["key_present"] is False
    assert path.exists()

    status, payload = call(backend, "GET", "/api/profiles")
    assert status == 200 and len(payload["profiles"]) == 1

    status, payload = call(backend, "DELETE", "/api/profiles/" + profile["id"])
    assert status == 200 and payload["ok"] is True
    status, payload = call(backend, "DELETE", "/api/profiles/" + profile["id"])
    assert status == 404


def test_invalid_profile_is_rejected(tmp_path):
    backend = make_backend(tmp_path / "providers.json")
    bad = dict(LOCAL_PROFILE, base_url="http://api.example.com/v1")
    status, payload = call(backend, "POST", "/api/profiles", body=bad)
    assert status == 400 and payload["ok"] is False


class ModelsServer:
    def __init__(self):
        owner = self
        self.seen = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.seen.append((self.path, self.headers.get("Authorization")))
                body = json.dumps({"data": [{"id": "qwen2.5:7b"}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self):
        return self.server.server_port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


def test_connection_test_endpoint(tmp_path):
    with ModelsServer() as upstream:
        backend = make_backend(tmp_path / "providers.json")
        profile = dict(LOCAL_PROFILE, base_url="http://127.0.0.1:%d/v1" % upstream.port)
        status, saved = call(backend, "POST", "/api/profiles", body=profile)
        assert status == 200, saved
        profile_id = saved["profile"]["id"]
        status, payload = call(backend, "POST", "/api/profiles/%s/test" % profile_id)
        assert status == 200, payload
        assert payload["ok"] is True
        assert payload["models"] == ["qwen2.5:7b"]
        assert payload["latency_s"] >= 0


def test_connection_test_reports_unreachable(tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    backend = make_backend(tmp_path / "providers.json")
    profile = dict(LOCAL_PROFILE, base_url="http://127.0.0.1:%d/v1" % port)
    status, saved = call(backend, "POST", "/api/profiles", body=profile)
    profile_id = saved["profile"]["id"]
    status, payload = call(backend, "POST", "/api/profiles/%s/test" % profile_id)
    assert status in (400, 502)
    assert payload["ok"] is False

