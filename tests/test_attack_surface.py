"""Hostile but syntactically legal HTTP values must never crash the edge layer."""
import asyncio
import json

import pytest

from providers import ProviderSpec
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.auth import bearer_token, origin_allowed, token_matches
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.edge.models import models_payload
from tokensfree.router.admission import admit

TOKEN = "a" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})


def backend_with_specs(specs):
    names = frozenset(spec.name for spec in specs)
    services = Services(
        specs=tuple(specs),
        adapters={},
        admit=lambda spec: admit(spec, CONFIG, names, 100),
        authorized=names,
    )
    return MemoryBackend(config=CONFIG, services=services, port=8765, token=TOKEN)


def request(backend, *, headers=None, method="GET", path="/v1/models", body=b""):
    base = {"host": "127.0.0.1:" + str(backend.port), "authorization": "Bearer " + TOKEN}
    base.update(headers or {})
    return EdgeRequest(method=method, path=path, headers=base, body=body)


def handle(backend, **kwargs):
    return asyncio.run(backend.handle(request(backend, **kwargs)))


@pytest.fixture()
def backend():
    return backend_with_specs([ProviderSpec("alpha", "mock-a", "mock_zero_cost", 10 ** 9)])


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer 中文令牌",
        "Bearer   spaced-token  ",
        "bearer " + "x" * 5000,
        "Bearer \x00\x01",
        "Bearer " + "\u2028\u2029",
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "",
        "Bearer\t" + TOKEN,
        "Bearer " + TOKEN + " extra",
    ],
)
def test_weird_authorization_values_are_just_unauthorized(backend, authorization):
    response = handle(backend, headers={"authorization": authorization})
    assert response.status in (200, 401)
    if response.status == 401:
        assert json.loads(response.body)["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    "host",
    ["", "127.0.0.1:1", "127.0.0.1:" + str(8765) + "x", "localhost", "LOCALHOST:8765", "127.0.0.1:8765.", "中文:8765"],
)
def test_weird_host_values_are_rejected_not_fatal(backend, host):
    response = handle(backend, headers={"host": host})
    assert response.status == 403
    assert json.loads(response.body)["error"]["code"] == "origin_rejected"


def test_origin_checks_accept_only_same_origin(backend):
    assert origin_allowed("127.0.0.1:8765", None, 8765)
    assert origin_allowed("localhost:8765", "http://localhost:8765", 8765)
    for origin in ["https://localhost:8765", "http://127.0.0.1:8766", "http://evil.example", "", "中文", "null"]:
        assert origin_allowed("127.0.0.1:8765", origin, 8765) is False


def test_bearer_and_token_matching_are_memory_safe():
    assert bearer_token("Bearer abc") == "abc"
    assert bearer_token("bearer abc") == "abc"
    assert bearer_token("Bearer 中文") == "中文"
    assert bearer_token("Basic abc") is None
    assert bearer_token(None) is None
    assert token_matches("x" * 32, "x" * 32)
    assert not token_matches("x" * 32, "x" * 31)
    assert not token_matches("中文" * 8, "x" * 32)
    assert not token_matches("", "")
    assert not token_matches("x" * 32, None)


def test_deeply_nested_json_is_a_400_not_a_crash(backend):
    nested = b"[" * 2000 + b"]" * 2000
    payload = b'{"model":"tokensfree-auto","messages":' + nested + b"}"
    response = handle(backend, method="POST", path="/v1/chat/completions", body=payload)
    assert response.status == 400
    assert json.loads(response.body)["error"]["code"] == "invalid_request"


def test_non_finite_json_numbers_are_rejected(backend):
    payload = b'{"model":"tokensfree-auto","messages":[{"role":"user","content":"hi"}],"max_tokens":1e400}'
    response = handle(backend, method="POST", path="/v1/chat/completions", body=payload)
    assert response.status == 400


def test_models_payload_rejects_non_specs():
    with pytest.raises(TypeError):
        models_payload([{"id": "fake"}])
    assert models_payload(()) == {"object": "list", "data": []}



def test_multibyte_idempotency_keys_are_bounded_by_characters_not_bytes():
    from tokensfree.adapters.base import ChatResult
    from tokensfree.edge.app_core import handle_chat
    from tokensfree.store.db import connect, migrate
    from tokensfree.store.repos import IdempotencyRepo

    class Echo:
        capabilities = frozenset({"text"})

        async def complete(self, request, admission):
            return ChatResult(content="ok")

    spec = ProviderSpec("alpha", "mock-a", "mock_zero_cost", 10 ** 9)
    connection = connect(":memory:")
    migrate(connection)
    try:
        names = frozenset({spec.name})
        services = Services(
            specs=(spec,),
            adapters={spec.name: Echo()},
            admit=lambda candidate: admit(candidate, CONFIG, names, 100),
            authorized=names,
            idempotency=IdempotencyRepo(connection),
        )
        payload = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}
        accepted = asyncio.run(handle_chat(payload, services=services, request_id="req-1", idempotency_key="中" * 200))
        assert accepted.status == 200
        rejected = asyncio.run(handle_chat(payload, services=services, request_id="req-2", idempotency_key="中" * 201))
        assert rejected.status == 400
        assert rejected.body["error"]["code"] == "invalid_request"
    finally:
        connection.close()
