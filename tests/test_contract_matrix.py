"""Contract matrix: rejection never reaches an adapter, malformed input is stable.

These tests replace unittest.mock.patch on server internals with an injected
adapter so the assertion is about behaviour, not about implementation details.
"""
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from providers import MockAdapter
from server import make_server


class CountingAdapter:
    calls = []

    def __init__(self, outcome):
        self.outcome = outcome

    def complete(self, messages):
        CountingAdapter.calls.append(self.outcome)
        return MockAdapter(self.outcome).complete(messages)


def start_server(**kwargs):
    server = make_server(0, adapter_factory=CountingAdapter, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def request(server, method, path, payload=None, raw=None, headers=None):
    body = raw if raw is not None else (None if payload is None else json.dumps(payload).encode())
    merged = {"Authorization": "Bearer " + server.token, "Content-Type": "application/json"}
    merged.update(headers or {})
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(method, path, body, merged)
    response = connection.getresponse()
    data = response.read(70000)
    status = response.status
    connection.close()
    return status, data


def test_rejected_requests_never_reach_adapter():
    CountingAdapter.calls = []
    server, thread = start_server()
    try:
        server.gateway.reset("success")
        status, _ = request(server, "POST", "/v1/chat/completions", {
            "model": "tokensfree-auto",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
        })
        assert status == 400
        assert CountingAdapter.calls == []
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_malformed_json_is_stable_400():
    server, thread = start_server()
    try:
        status, data = request(server, "POST", "/v1/chat/completions", raw=b'{"broken":')
        body = json.loads(data)
        assert status == 400
        assert body["error"]["code"] == "invalid_request"
        assert body["error"]["request_id"]
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_unknown_model_is_model_not_found_or_invalid_request():
    server, thread = start_server()
    try:
        status, data = request(server, "POST", "/v1/chat/completions", {
            "model": "paid-model",
            "messages": [{"role": "user", "content": "x"}],
        })
        assert status == 400
        assert json.loads(data)["error"]["code"] in {"invalid_request", "model_not_found"}
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_concurrent_same_key_runs_once():
    CountingAdapter.calls = []
    server, thread = start_server()
    try:
        gateway = server.gateway  # default scenario: fallback = [exhausted, success]
        payload = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "x"}]}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: gateway.dispatch(dict(payload), "same-key"), range(20)))
        assert CountingAdapter.calls == ["exhausted", "success"]
        assert all(result == results[0] for result in results)
    finally:
        server.shutdown(); server.server_close(); thread.join()
