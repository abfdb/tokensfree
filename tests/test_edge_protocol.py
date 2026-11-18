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


def test_dsh_default_max_tokens_is_accepted():
    # DSH asks for 128000 tokens by default; rejecting it broke the real client.
    parsed = parse_chat_payload(payload(max_tokens=128000))
    assert parsed.request.max_tokens == 128000
    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(max_tokens=1_000_001))


def test_assistant_content_null_with_tool_calls_is_accepted():
    messages = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "body"},
    ]
    parsed = parse_chat_payload(payload(messages=messages))
    assert parsed.request.messages[1]["content"] == ""
    assert parsed.request.messages[1]["tool_calls"][0]["id"] == "call_1"
    assert parsed.requires_tools is True



def test_valid_assistant_tool_calls_are_accepted():
    messages = [
        {"role": "user", "content": "read it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "body"},
    ]
    parsed = parse_chat_payload(payload(messages=messages))
    assert parsed.requires_tools is True


@pytest.mark.parametrize(
    "broken",
    [
        [{"id": "x", "type": "function"}],
        [{"id": "", "type": "function", "function": {"name": "read", "arguments": ""}}],
        [{"id": "x", "type": "function", "function": {"arguments": ""}}],
        [{"id": "x", "type": "function", "function": {"name": "read", "arguments": 5}}],
        ["not-a-dict"],
    ],
)
def test_malformed_tool_calls_are_rejected(broken):
    messages = [{"role": "assistant", "content": "", "tool_calls": broken}]
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(messages=messages))


@pytest.mark.parametrize("choice", ["auto", "none", "required", {"type": "function", "function": {"name": "read"}}])
def test_supported_tool_choice_shapes_are_accepted(choice):
    parsed = parse_chat_payload(payload(tool_choice=choice, tools=[{"type": "function", "function": {"name": "read"}}]))
    assert parsed.request.tool_choice == choice


@pytest.mark.parametrize("choice", ["sometimes", 3, {"type": "other"}, {"function": {"name": "read"}}, {"type": "function"}])
def test_unsupported_tool_choice_shapes_are_rejected(choice):
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(tool_choice=choice))



@pytest.mark.parametrize("role", [[], {}, ["user"], {"user": True}, 3, True])
def test_non_string_roles_are_rejected_without_a_type_error(role):
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(messages=[{"role": role, "content": "hi"}]))



def test_sampling_parameters_are_kept():
    parsed = parse_chat_payload(
        payload(
            temperature=0.2,
            top_p=0.9,
            stop=["END", "STOP"],
            seed=7,
            response_format={"type": "json_object"},
        )
    )
    assert parsed.request.temperature == 0.2
    assert parsed.request.top_p == 0.9
    assert parsed.request.stop == ("END", "STOP")
    assert parsed.request.seed == 7
    assert parsed.request.response_format == {"type": "json_object"}


def test_single_stop_string_is_normalized():
    parsed = parse_chat_payload(payload(stop="END"))
    assert parsed.request.stop == ("END",)


def test_json_schema_response_format_is_kept():
    parsed = parse_chat_payload(
        payload(response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": {"type": "object"}}})
    )
    assert parsed.request.response_format["json_schema"]["name"] == "answer"


@pytest.mark.parametrize(
    "overrides",
    [
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": "0.5"},
        {"temperature": True},
        {"top_p": 1.5},
        {"top_p": -0.1},
        {"stop": []},
        {"stop": ["a", "b", "c", "d", "e"]},
        {"stop": [1]},
        {"stop": ""},
        {"seed": -1},
        {"seed": 1.5},
        {"seed": True},
        {"response_format": "json"},
        {"response_format": {"type": "bogus"}},
        {"response_format": {"type": "json_schema"}},
        {"response_format": {"type": "json_schema", "json_schema": {"name": ""}}},
    ],
)
def test_invalid_sampling_parameters_are_rejected(overrides):
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(**overrides))


def test_n_greater_than_one_is_unsupported():
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected) as caught:
        parse_chat_payload(payload(n=2))
    assert caught.value.code.value == "unsupported_capability"
    parsed = parse_chat_payload(payload(n=1))
    assert parsed.request.model == base_model()


def base_model():
    return "tokensfree-auto"



def test_stream_options_include_usage_is_parsed():
    parsed = parse_chat_payload(payload(stream=True, stream_options={"include_usage": True}))
    assert parsed.include_usage is True
    assert parsed.request.stream_include_usage is True


def test_stream_options_without_usage_keeps_it_off():
    parsed = parse_chat_payload(payload(stream=True, stream_options={"include_usage": False}))
    assert parsed.include_usage is False
    assert parsed.request.stream_include_usage is False


def test_chat_response_created_is_a_real_timestamp():
    from tokensfree.edge.protocol import build_chat_response

    body = build_chat_response(request_id="req-1", model="m", content="hi")
    assert isinstance(body["created"], int) and body["created"] > 0



def test_max_completion_tokens_alias_is_accepted():
    parsed = parse_chat_payload(payload(max_completion_tokens=200))
    assert parsed.request.max_tokens == 200


def test_both_token_limit_aliases_are_rejected():
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(max_tokens=100, max_completion_tokens=200))


@pytest.mark.parametrize("value", [0, -5, 1_000_001, "100", True, 1.5])
def test_invalid_max_completion_tokens_are_rejected(value):
    from tokensfree.edge.protocol import RequestRejected

    with pytest.raises(RequestRejected):
        parse_chat_payload(payload(max_completion_tokens=value))


def test_penalties_are_kept_and_validated():
    from tokensfree.edge.protocol import RequestRejected

    parsed = parse_chat_payload(payload(presence_penalty=-0.5, frequency_penalty=1.2))
    assert parsed.request.presence_penalty == -0.5
    assert parsed.request.frequency_penalty == 1.2
    for overrides in ({"presence_penalty": -3}, {"frequency_penalty": 2.5}, {"presence_penalty": "x"}):
        with pytest.raises(RequestRejected):
            parse_chat_payload(payload(**overrides))
