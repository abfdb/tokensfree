"""Codify the intentional differences between the legacy server and the new backend.

If one of these changes, the parity document must change too.
"""
import asyncio
import json
import threading

from providers import ProviderSpec
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.router.admission import admit
from server import make_server

TOKEN = "p" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
SPEC = ProviderSpec("mock-alpha", "mock-a", "mock_zero_cost", 200)


def new_backend():
    names = frozenset({SPEC.name})
    services = Services(
        specs=(SPEC,),
        adapters={},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
    )
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)


def call(backend, method, path):
    headers = {"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN}
    response = asyncio.run(backend.handle(EdgeRequest(method=method, path=path, headers=headers, body=b"")))
    return response.status, response.body


def test_new_backend_has_no_session_endpoint():
    # The new backend uses a stable configured token instead of /api/session.
    status, body = call(new_backend(), "GET", "/api/session")
    assert status == 404
    assert json.loads(body)["error"]["code"] == "not_found"


def test_legacy_server_still_offers_a_session_token():
    server = make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import http.client

        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", "/api/session", headers={"Host": "127.0.0.1:" + str(server.server_port)})
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        assert response.status == 200
        assert isinstance(payload["token"], str) and payload["token"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_new_models_catalog_uses_provider_model_ids():
    status, body = call(new_backend(), "GET", "/v1/models")
    assert status == 200
    assert json.loads(body)["data"][0]["id"] == "mock-a"
