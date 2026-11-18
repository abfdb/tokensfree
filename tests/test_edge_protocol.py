"""Framework-free edge protocol: parse an OpenAI chat payload, build responses."""
import pytest

from tokensfree.adapters.base import NormalizedError
from tokensfree.edge.protocol import (
    ParsedChat,
    RequestRejected,
    build_chat_response,
    build_error,
    map_upstream_error,
    parse_chat_payload,
)


def payload(**overrides):
    base = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}
    base.update(overrides)
    return base


def test_minimal_payload_parses():
    parsed = parse_chat_payload(payload())
    assert isinstance(parsed, ParsedChat)
    assert parsed.request.model == "tokensfree-auto"
    assert parsed.request.messages == ({"role": "user", "content": "hi"},)
    assert parsed.stream is False
    assert parsed.requires_tools is False
    assert parsed.capabilities == frozenset({"text"})


def test_stream_and_max_tokens_are_kept():
    parsed = parse_chat_payload(payload(stream=True, max_tokens=64))
    assert parsed.stream is True
    assert parsed.request.max_tokens == 64
    assert "stream" in parsed.capabilities


def test_tools_raise_required_capability():
    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    parsed = parse_chat_payload(payload(tools=tools))
    assert parsed.requires_tools is True
    assert "tools" in parsed.capabilities
    assert parsed.request.tools == tuple(tools)


def test_developer_role_mapping_is_explicit():
    mapped = parse_chat_payload(payload(messages=[{"role": "developer", "content": "be nice"}]))
    assert mapped.request.messages[0]["role"] == "system"
    with pytest.raises(RequestRejected) as caught:
        parse_chat_payload(payload(messages=[{"role": "developer", "content": "x"}]), developer_role_policy="reject")
    assert caught.value.code.value == "unsupported_capability"


def test_tool_message_requires_tool_call_id():
    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(messages=[{"role": "tool", "content": "result"}]))
    parsed = parse_chat_payload(payload(messages=[{"role": "tool", "tool_call_id": "call_1", "content": "ok"}]))
    assert parsed.request.messages[0]["tool_call_id"] == "call_1"
    assert "tools" in parsed.capabilities


@pytest.mark.parametrize("bad", [
    "not-a-dict",
    {"messages": [{"role": "user", "content": "x"}]},
    payload(messages=[]),
    payload(messages=[{"role": "robot", "content": "x"}]),
    payload(messages=[{"role": "user", "content": 5}]),
    payload(messages=[{"role": "user", "content": "x" * 4001}]),
    payload(messages=[{"role": "user", "content": "x"}], stream="yes"),
    payload(max_tokens=0),
    payload(model=5),
])
def test_invalid_payload_is_rejected_with_a_code(bad):
    with pytest.raises(RequestRejected) as caught:
        parse_chat_payload(bad)
    assert caught.value.code.value == "invalid_request"


def test_upstream_error_mapping_is_table_driven():
    cases = [
        ("auth_invalid", "upstream_auth_failed"),
        ("billing_required", "policy_blocked"),
        ("rate_limited", "upstream_rate_limited"),
        ("quota_exhausted", "no_free_provider"),
        ("redirect_rejected", "upstream_error"),
        ("upstream_error", "upstream_error"),
        ("timeout", "upstream_timeout"),
        ("disconnected", "upstream_disconnected"),
        ("refusal", "refusal"),
    ]
    for upstream_code, client_code in cases:
        error = NormalizedError(upstream_code, retryable=False)
        assert map_upstream_error(error).value == client_code


def test_response_shape_matches_legacy_server():
    body = build_chat_response(request_id="req-1", model="mock-alpha", content="hello",
                               usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "estimated": True})
    assert body["id"] == "req-1"
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "hello"}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["estimated"] is True


def test_build_error_reuses_codes():
    status, body = build_error("refusal", "req-1".rjust(1))
    assert status == 422
    assert body["error"]["code"] == "refusal"
