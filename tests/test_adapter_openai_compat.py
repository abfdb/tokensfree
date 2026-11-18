"""Generic OpenAI-compatible adapter: loopback fixture only, default disabled."""
import asyncio
import json

import pytest

from tokensfree.adapters.base import Admission, ChatRequest, ChatResult, NormalizedError, _ADMISSION_TOKEN
from tokensfree.adapters.openai_compat import OpenAICompatAdapter
from upstream_support import JsonUpstream, SSEUpstream

KEY = "sk-test-not-a-real-key"
SUCCESS = json.dumps(
    {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
)
SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def admission():
    return Admission(provider="openai-compat", capabilities=frozenset({"text", "stream"}), simulation=True, _token=_ADMISSION_TOKEN)


def request(**overrides):
    return ChatRequest(model="m-1", messages=({"role": "user", "content": "hi"},), **overrides)


def base_url(upstream, path="/v1"):
    return "http://127.0.0.1:" + str(upstream.port) + path


@pytest.fixture()
def json_upstream():
    server = JsonUpstream(SUCCESS)
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def sse_upstream():
    server = SSEUpstream(SSE)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_complete_returns_chat_result(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    result = asyncio.run(adapter.complete(request(tools=({"type": "function", "function": {"name": "read"}},)), admission()))
    assert isinstance(result, ChatResult)
    assert result.content == "hello"
    assert result.usage["total_tokens"] == 3
    sent = json_upstream.requests[-1]
    assert sent["model"] == "m-1"
    assert sent["tools"][0]["function"]["name"] == "read"
    assert sent["messages"][0]["content"] == "hi"
    assert json_upstream.headers["Authorization"] == "Bearer " + KEY


def test_upstream_errors_are_normalized(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(status=401, body="{}")
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "auth_invalid"
    assert KEY not in str(caught.value)


def test_rate_limit_uses_retry_after(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(status=429, body="{}")
    json_upstream.response_headers = {"Retry-After": "5"}
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True


def test_malformed_json_is_invalid_response(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(body="not-json")
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "invalid_response"
    assert caught.value.retryable is False


def test_stream_yields_events(sse_upstream):
    adapter = OpenAICompatAdapter(base_url(sse_upstream), KEY)

    async def run_all():
        return [event async for event in adapter.stream(request(), admission())]

    events = asyncio.run(run_all())
    assert [event.kind for event in events] == ["delta", "delta", "finish", "done"]
    assert events[1].delta == {"content": "hi"}


def test_transport_target_rules():
    with pytest.raises(ValueError):
        OpenAICompatAdapter("http://example.com/v1", KEY)          # plaintext remote refused
    with pytest.raises(ValueError):
        OpenAICompatAdapter("https://example.com/v1", KEY)         # remote requires allow_remote
    assert OpenAICompatAdapter("https://example.com/v1", KEY, allow_remote=True) is not None
    with pytest.raises(ValueError):
        OpenAICompatAdapter("http://127.0.0.1:1/v1", "")           # empty key
    with pytest.raises(ValueError):
        OpenAICompatAdapter("ftp://127.0.0.1/v1", KEY)



def test_stream_deadline_stops_a_dripping_upstream():
    server = SSEUpstream(SSE, delay_s=0.2)
    server.start()
    try:
        adapter = OpenAICompatAdapter(base_url(server), KEY, max_stream_s=0.01)

        async def run_all():
            return [event async for event in adapter.stream(request(), admission())]

        with pytest.raises(NormalizedError) as caught:
            asyncio.run(run_all())
        assert caught.value.code == "timeout"
        assert caught.value.retryable is True
    finally:
        server.stop()



@pytest.mark.parametrize("code", ["1302", "1305"])
def test_429_rate_limit_error_codes_are_retryable(json_upstream, code):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(status=429, body=json.dumps({"error": {"code": code, "message": "limited"}}))
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True


@pytest.mark.parametrize("code", ["1113", "insufficient_quota"])
def test_429_quota_error_codes_are_not_retryable(json_upstream, code):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(status=429, body=json.dumps({"error": {"code": code, "message": "no balance"}}))
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "quota_exhausted"
    assert caught.value.retryable is False


def test_429_without_error_body_keeps_the_generic_mapping(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(status=429, body="")
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "quota_exhausted"



TOOL_CALLS_BODY = json.dumps(
    {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "read", "arguments": "{\"path\":\"a.txt\"}"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
)


def test_tool_calls_are_returned(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(body=TOOL_CALLS_BODY)
    result = asyncio.run(adapter.complete(request(), admission()))
    assert result.content == ""
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read"
    assert call["function"]["arguments"] == '{"path":"a.txt"}'


def test_malformed_tool_calls_are_invalid_response(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    json_upstream.configure(
        body=json.dumps({"choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{"id": 1}]}, "finish_reason": "tool_calls"}]})
    )
    with pytest.raises(NormalizedError) as caught:
        asyncio.run(adapter.complete(request(), admission()))
    assert caught.value.code == "invalid_response"



def test_sampling_parameters_are_forwarded(json_upstream):
    adapter = OpenAICompatAdapter(base_url(json_upstream), KEY)
    request_with_params = request(
        temperature=0.2,
        top_p=0.9,
        stop=("END",),
        seed=7,
        response_format={"type": "json_object"},
    )
    asyncio.run(adapter.complete(request_with_params, admission()))
    sent = json_upstream.requests[-1]
    assert sent["temperature"] == 0.2
    assert sent["top_p"] == 0.9
    assert sent["stop"] == ["END"]
    assert sent["seed"] == 7
    assert sent["response_format"] == {"type": "json_object"}
