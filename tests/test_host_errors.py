"""Unexpected failures inside the app must not leave the client without a response."""
import http.client
import json

from host_support import HostRunner
from tokensfree.config import load_config
from tokensfree.edge.asgi import make_asgi_app
from tokensfree.edge.backend import EdgeResponse

TOKEN = "e" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})


def request(port, path="/v1/models"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path, headers={"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + TOKEN})
    response = connection.getresponse()
    payload = response.read()
    result = (response.status, payload)
    connection.close()
    return result


class ExplodingBackend:
    config = CONFIG

    async def handle(self, request):
        raise RuntimeError("backend bug")


class StreamingThenFailingBackend:
    config = CONFIG

    async def handle(self, request):
        async def stream():
            yield b"data: first\n\n"
            raise RuntimeError("upstream blew up mid-stream")

        return EdgeResponse(status=200, headers={"content-type": "text/event-stream"}, stream=stream())


def test_unexpected_backend_error_becomes_internal_error():
    runner = HostRunner(make_asgi_app(ExplodingBackend()))
    port = runner.start()
    try:
        status, payload = request(port)
        assert status == 500
        assert json.loads(payload)["error"]["code"] == "internal_error"
        # the host keeps serving: a second request fails the same way, not by hanging
        status, _ = request(port)
        assert status == 500
    finally:
        runner.stop()


def test_failure_after_streaming_start_closes_without_a_second_response():
    runner = HostRunner(make_asgi_app(StreamingThenFailingBackend()))
    port = runner.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("GET", "/v1/models", headers={"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + TOKEN})
        response = connection.getresponse()
        assert response.status == 200
        try:
            payload = response.read()
        except http.client.IncompleteRead as error:
            payload = error.partial
        connection.close()
        assert b"data: first" in payload
        assert b"[DONE]" not in payload
    finally:
        runner.stop()
