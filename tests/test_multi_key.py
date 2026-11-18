"""Multiple free-tier keys: per-key providers, fallback on a retryable limit."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import build_real_runtime, real_keys_from_env, real_specs_from_env

TOKEN = "m" * 32
CONFIG_ON = load_config({"TOKENSFREE_ENABLE_REAL": "1", "TOKENSFREE_LOCAL_TOKEN": TOKEN})
MODEL = "real-model"
PROVIDER = "zhipu-test"
KEY_ONE = "key-one-0001"
KEY_TWO = "key-two-0002"
OK_BODY = json.dumps(
    {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "from key two"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
)


class KeyAwareUpstream:
    """429 with Retry-After for key one; 200 for any other key."""

    def __init__(self):
        self.calls = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                authorization = self.headers.get("Authorization", "")
                owner.calls.append(authorization)
                if KEY_ONE in authorization:
                    body = b'{"error":{"code":"1302","message":"limited"}}'
                    self.send_response(429)
                    self.send_header("Retry-After", "5")
                else:
                    body = OK_BODY.encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = None

    @property
    def port(self):
        return self._server.server_port

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(10)


def env_for(port, keys, *, key=None):
    values = {
        "TOKENSFREE_REAL_PROVIDER": PROVIDER,
        "TOKENSFREE_REAL_BASE_URL": "http://127.0.0.1:" + str(port) + "/v1",
        "TOKENSFREE_REAL_MODEL": MODEL,
        "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT": "2000",
    }
    if keys is not None:
        values["TOKENSFREE_REAL_API_KEYS"] = keys
    if key is not None:
        values["TOKENSFREE_REAL_API_KEY"] = key
    return values


def test_single_key_keeps_the_plain_name():
    specs, keys = real_specs_from_env(env_for(1, None, key=KEY_ONE), clock=lambda: 1000.0)
    assert [spec.name for spec in specs] == [PROVIDER]
    assert keys == (KEY_ONE,)


def test_multiple_keys_become_numbered_providers():
    specs, keys = real_specs_from_env(env_for(1, KEY_ONE + "," + KEY_TWO), clock=lambda: 1000.0)
    assert [spec.name for spec in specs] == [PROVIDER, PROVIDER + "-2"]
    assert all(spec.model == MODEL and spec.simulation is False for spec in specs)
    assert keys == (KEY_ONE, KEY_TWO)


def test_both_key_variables_are_rejected():
    with pytest.raises(ValueError) as caught:
        real_keys_from_env({"TOKENSFREE_REAL_API_KEY": "a", "TOKENSFREE_REAL_API_KEYS": "b"})
    assert "only one of" in str(caught.value)


def test_key_list_is_bounded_and_empty_entries_are_ignored():
    with pytest.raises(ValueError):
        real_keys_from_env({"TOKENSFREE_REAL_API_KEYS": " , ".join(["k"] * 9)})
    assert real_keys_from_env({"TOKENSFREE_REAL_API_KEYS": "a,,b"}) == ("a", "b")


def test_rate_limited_first_key_falls_back_to_the_second():
    upstream = KeyAwareUpstream()
    upstream.start()
    try:
        runtime = build_real_runtime(config=CONFIG_ON, env=env_for(upstream.port, KEY_ONE + "," + KEY_TWO), clock=lambda: 1000.0)
        outcome = asyncio.run(
            handle_chat(
                {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
                services=runtime.services,
                request_id="req-multi",
            )
        )
        assert outcome.status == 200
        assert outcome.provider == PROVIDER + "-2"
        assert outcome.body["choices"][0]["message"]["content"] == "from key two"
        assert any(KEY_ONE in call for call in upstream.calls)
        assert any(KEY_TWO in call for call in upstream.calls)
        assert outcome.body["tokensfree"]["trace"][0][1] == "rate_limited"
    finally:
        upstream.stop()


def test_evidence_applies_to_every_key():
    upstream = KeyAwareUpstream()
    upstream.start()
    try:
        with pytest.raises(ValueError) as caught:
            build_real_runtime(
                config=CONFIG_ON,
                env=env_for(upstream.port, KEY_ONE + "," + KEY_TWO) | {"TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT": "500"},
                clock=lambda: 1000.0,
            )
        assert "past" in str(caught.value)
    finally:
        upstream.stop()
