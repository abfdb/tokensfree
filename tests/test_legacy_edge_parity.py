"""The legacy handler must agree with the shared edge primitives."""
import http.client

import pytest

from server import make_server
from tokensfree.edge.auth import origin_allowed

PAYLOAD = b'{"model":"tokensfree-auto","messages":[{"role":"user","content":"x"}]}'


@pytest.fixture()
def server():
    srv = make_server(0)
    import threading

    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join()


def request(server, path, *, host=None, origin=None, authorization=None, body=None):
    headers = {}
    if host is not None:
        headers["Host"] = host
    if origin is not None:
        headers["Origin"] = origin
    if authorization is not None:
        headers["Authorization"] = authorization
    headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request("POST" if body else "GET", path, body, headers)
    response = connection.getresponse()
    response.read()
    status = response.status
    connection.close()
    return status


def host_for(port):
    return "127.0.0.1:" + str(port)


def test_host_origin_matrix_matches_shared_rules(server):
    port = server.server_port
    for host in (host_for(port), "localhost:" + str(port), "evil.example", "127.0.0.1:1"):
        for origin in (None, "http://" + host_for(port), "http://localhost:" + str(port), "https://evil.example"):
            expected = origin_allowed(host, origin, port)
            status = request(server, "/api/session", host=host, origin=origin)
            assert (status == 200) is expected, (host, origin, status)


def test_bearer_scheme_is_case_insensitive(server):
    token = None
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request("GET", "/api/session")
    response = connection.getresponse()
    import json

    token = json.loads(response.read())["token"]
    connection.close()
    assert request(server, "/v1/chat/completions", authorization="Bearer " + token, body=PAYLOAD) == 200
    assert request(server, "/v1/chat/completions", authorization="bearer " + token, body=PAYLOAD) == 200
    assert request(server, "/v1/chat/completions", authorization="Basic " + token, body=PAYLOAD) == 401
    assert request(server, "/v1/chat/completions", authorization="Bearer wrong-token", body=PAYLOAD) == 401
