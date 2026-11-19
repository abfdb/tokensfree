"""Browser console served by the new backend: static assets, CSP, session token."""
import asyncio
import http.client
import json
from pathlib import Path

import pytest

from host_support import HostRunner
from tokensfree.config import load_config
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.runtime import build_mock_runtime

TOKEN = "w" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
WEB_DIR = Path(__file__).resolve().parents[1] / "tokensfree" / "edge" / "web"


@pytest.fixture(scope="module")
def backend():
    services = build_mock_runtime(config=CONFIG).services
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN, static_dir=WEB_DIR)


@pytest.fixture(scope="module")
def plain_backend():
    services = build_mock_runtime(config=CONFIG).services
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)


def request(backend, method, path, *, token=None, host="127.0.0.1:8765"):
    headers = {"host": host}
    if token is not None:
        headers["authorization"] = "Bearer " + token
    return asyncio.run(backend.handle(EdgeRequest(method=method, path=path, headers=headers)))


def test_index_is_served_without_a_token(backend):
    response = request(backend, "GET", "/")
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    body = response.body.decode("utf-8")
    assert "<title>tokensfree 控制台</title>" in body
    assert 'src="/web/app.js"' in body
    assert "<script>" not in body  # no inline script: CSP stays strict


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/web/app.js", "application/javascript"),
        ("/web/style.css", "text/css"),
    ],
)
def test_static_assets_are_served(backend, path, content_type):
    response = request(backend, "GET", path)
    assert response.status == 200
    assert response.headers["content-type"].startswith(content_type)


@pytest.mark.parametrize(
    "path",
    ["/web/../config.py", "/web/..%2fconfig.py", "/web/nope.js", "/web/"],
)
def test_static_path_traversal_and_unknown_files_are_404(backend, path):
    response = request(backend, "GET", path)
    assert response.status == 404


def test_static_is_get_only(backend):
    response = request(backend, "POST", "/")
    assert response.status == 405
    assert response.headers["allow"] == "GET"


def test_session_endpoint_issues_the_local_token(backend):
    response = request(backend, "GET", "/api/session")
    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["token"] == TOKEN
    assert payload["base_url"] == "http://127.0.0.1:8765/v1"
    assert request(backend, "POST", "/api/session").status == 405


def test_session_endpoint_rejects_a_foreign_host(backend):
    response = request(backend, "GET", "/api/session", host="evil.example:8765")
    assert response.status == 403


def test_api_still_requires_the_token(backend):
    assert request(backend, "GET", "/v1/models").status == 401
    assert request(backend, "GET", "/v1/models", token=TOKEN).status == 200
    assert request(backend, "GET", "/api/status").status == 401


def test_console_is_absent_without_a_static_dir(plain_backend):
    # An API-only deployment keeps the stricter behaviour: no UI and no token endpoint.
    assert request(plain_backend, "GET", "/").status == 404
    assert request(plain_backend, "GET", "/api/session").status == 404


def test_console_works_over_a_real_socket(backend):
    runner = HostRunner(make_asgi_app(backend), max_body=65536)
    port = runner.start()
    backend.port = port
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/", headers={"Host": "127.0.0.1:" + str(port)})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert response.status == 200
        assert "text/html" in response.getheader("content-type")
        assert "tokensfree" in body
        connection.close()

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/api/session", headers={"Host": "127.0.0.1:" + str(port)})
        response = connection.getresponse()
        payload = json.loads(response.read())
        assert payload["token"] == TOKEN
        connection.close()
    finally:
        runner.stop()
        backend.port = 8765


def test_console_includes_the_channel_manager(backend):
    index = request(backend, "GET", "/").body.decode("utf-8")
    assert 'id="profile-list"' in index
    assert 'id="profile-form"' in index
    script = request(backend, "GET", "/web/app.js").body.decode("utf-8")
    assert "/api/profiles" in script
    assert "loadProfiles" in script
