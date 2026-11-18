"""Framework-free edge protocol.

Turns an OpenAI-shaped chat payload into the kernel's ChatRequest, and turns
kernel results/errors back into OpenAI-shaped bodies. No HTTP objects live here,
so the legacy stdlib server and the future async backend share one semantic.
"""
import time
from dataclasses import dataclass

from tokensfree.adapters.base import ChatRequest, NormalizedError
from tokensfree.errors import ErrorCode, http_error

ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool", "developer"})
DEFAULT_MAX_TOKENS = 1024
MAX_CONTRACT_TOKENS = 1_000_000

_UPSTREAM_TO_CLIENT = {
    "auth_invalid": ErrorCode.upstream_auth_failed,
    "billing_required": ErrorCode.policy_blocked,
    "rate_limited": ErrorCode.upstream_rate_limited,
    "quota_exhausted": ErrorCode.no_free_provider,
    "redirect_rejected": ErrorCode.upstream_error,
    "upstream_error": ErrorCode.upstream_error,
    "timeout": ErrorCode.upstream_timeout,
    "disconnected": ErrorCode.upstream_disconnected,
    "refusal": ErrorCode.refusal,
}


class RequestRejected(Exception):
    """Stable machine-readable rejection raised before any routing happens."""

    __slots__ = ("code",)

    def __init__(self, code):
        if not isinstance(code, ErrorCode):
            raise TypeError("RequestRejected requires an ErrorCode")
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class ParsedChat:
    request: ChatRequest
    stream: bool
    requires_tools: bool
    capabilities: frozenset
    include_usage: bool = False


def _reject():
    raise RequestRejected(ErrorCode.invalid_request)


def _parse_messages(messages, developer_role_policy, max_messages, max_content_chars):
    if not isinstance(messages, list) or not 1 <= len(messages) <= max_messages:
        _reject()
    normalized = []
    needs_tools = False
    for message in messages:
        if not isinstance(message, dict):
            _reject()
        role = message.get("role")
        content = message.get("content")
        # isinstance first: unhashable JSON values (list/dict) must not leak a TypeError.
        if not isinstance(role, str) or role not in ALLOWED_ROLES:
            _reject()
        if not isinstance(content, str):
            # OpenAI permits content:null when an assistant message carries tool_calls.
            tool_calls = message.get("tool_calls")
            if role == "assistant" and content is None and isinstance(tool_calls, list) and tool_calls:
                content = ""
            else:
                _reject()
        if len(content) > max_content_chars:
            _reject()
        if role == "developer":
            if developer_role_policy != "map":
                raise RequestRejected(ErrorCode.unsupported_capability)
            role = "system"
        entry = {"role": role, "content": content}
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            if not isinstance(tool_call_id, str) or not tool_call_id:
                _reject()
            entry["tool_call_id"] = tool_call_id
            needs_tools = True
        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            if role != "assistant" or not isinstance(tool_calls, list) or not tool_calls:
                _reject()
            for call in tool_calls:
                if not isinstance(call, dict):
                    _reject()
                if not isinstance(call.get("id"), str) or not call["id"]:
                    _reject()
                function = call.get("function")
                if not isinstance(function, dict):
                    _reject()
                if not isinstance(function.get("name"), str) or not function["name"]:
                    _reject()
                if not isinstance(function.get("arguments", ""), str):
                    _reject()
            entry["tool_calls"] = tool_calls
            needs_tools = True
        normalized.append(entry)
    return tuple(normalized), needs_tools


def _optional_number(payload, key, minimum, maximum):
    value = payload.get(key)
    if value is None:
        return None
    if type(value) is not int and type(value) is not float:
        _reject()
    if not minimum <= float(value) <= maximum:
        _reject()
    return float(value)


def _parse_stop(value):
    if value is None:
        return ()
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list) and 1 <= len(value) <= 4:
        candidates = value
    else:
        _reject()
    for candidate in candidates:
        if not isinstance(candidate, str) or not 1 <= len(candidate) <= 64:
            _reject()
    return tuple(candidates)


def _parse_response_format(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        _reject()
    kind = value.get("type")
    if kind not in ("text", "json_object", "json_schema"):
        _reject()
    if kind == "json_schema":
        schema = value.get("json_schema")
        if not isinstance(schema, dict):
            _reject()
        if not isinstance(schema.get("name"), str) or not schema["name"]:
            _reject()
    return dict(value)


def parse_chat_payload(payload, *, developer_role_policy="map", max_messages=32, max_content_chars=4000):
    if not isinstance(payload, dict):
        _reject()
    model = payload.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 128:
        _reject()
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        _reject()
    include_usage = False
    stream_options = payload.get("stream_options")
    if stream_options is not None:
        if not isinstance(stream_options, dict):
            _reject()
        raw_usage = stream_options.get("include_usage")
        if raw_usage is not None and not isinstance(raw_usage, bool):
            _reject()
        if raw_usage and not stream:
            _reject()
        include_usage = bool(raw_usage)
    max_tokens = payload.get("max_tokens")
    max_completion_tokens = payload.get("max_completion_tokens")
    if max_tokens is not None and max_completion_tokens is not None:
        _reject()  # ambiguous: the two aliases must not disagree
    if max_tokens is None:
        max_tokens = max_completion_tokens if max_completion_tokens is not None else DEFAULT_MAX_TOKENS
    if type(max_tokens) is not int or isinstance(max_tokens, bool) or not 1 <= max_tokens <= 1_000_000:
        _reject()
    messages, needs_tools_from_messages = _parse_messages(
        payload.get("messages"), developer_role_policy, max_messages, max_content_chars
    )
    tools = payload.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not tools or not all(isinstance(tool, dict) for tool in tools):
            _reject()
        tools = tuple(tools)
    else:
        tools = ()
    if "n" in payload and not (type(payload["n"]) is int and payload["n"] == 1):
        raise RequestRejected(ErrorCode.unsupported_capability)
    # Silent omission is worse than a clear rejection: these cannot be delivered.
    if payload.get("logprobs") is not None and not isinstance(payload.get("logprobs"), bool):
        _reject()
    if payload.get("logprobs") is True or payload.get("top_logprobs") is not None:
        raise RequestRejected(ErrorCode.unsupported_capability)
    if payload.get("modalities") is not None and payload.get("modalities") != ["text"]:
        raise RequestRejected(ErrorCode.unsupported_capability)
    if payload.get("audio") is not None:
        raise RequestRejected(ErrorCode.unsupported_capability)
    temperature = _optional_number(payload, "temperature", 0.0, 2.0)
    top_p = _optional_number(payload, "top_p", 0.0, 1.0)
    presence_penalty = _optional_number(payload, "presence_penalty", -2.0, 2.0)
    frequency_penalty = _optional_number(payload, "frequency_penalty", -2.0, 2.0)
    stop = _parse_stop(payload.get("stop"))
    seed = payload.get("seed")
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 2 ** 31 - 1):
        _reject()
    response_format = _parse_response_format(payload.get("response_format"))
    tool_choice = payload.get("tool_choice")
    if tool_choice is not None:
        if isinstance(tool_choice, str):
            if tool_choice not in ("none", "auto", "required"):
                _reject()
        elif isinstance(tool_choice, dict):
            function = tool_choice.get("function")
            if tool_choice.get("type") != "function" or not isinstance(function, dict):
                _reject()
            if not isinstance(function.get("name"), str) or not function["name"]:
                _reject()
        else:
            _reject()
    requires_tools = bool(tools) or needs_tools_from_messages
    capabilities = {"text"}
    if requires_tools:
        capabilities.add("tools")
    if stream:
        capabilities.add("stream")
    request = ChatRequest(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        stream=stream,
        tools=tools,
        tool_choice=tool_choice,
        temperature=temperature,
        top_p=top_p,
        stop=stop,
        seed=seed,
        response_format=response_format,
        stream_include_usage=include_usage,
        presence_penalty=presence_penalty,
        frequency_penalty=frequency_penalty,
    )
    return ParsedChat(
        request=request,
        stream=stream,
        requires_tools=requires_tools,
        capabilities=frozenset(capabilities),
        include_usage=include_usage,
    )


def map_upstream_error(error):
    if not isinstance(error, NormalizedError):
        raise TypeError("map_upstream_error requires a NormalizedError")
    return _UPSTREAM_TO_CLIENT.get(error.code, ErrorCode.upstream_error)


def build_chat_response(*, request_id, model, content, usage=None, finish_reason="stop", trace=None, tool_calls=(), created=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [dict(call) for call in tool_calls]
    body = {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()) if created is None else int(created),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    if usage is not None:
        body["usage"] = usage
    if trace is not None:
        body["tokensfree"] = {"trace": [list(attempt) for attempt in trace]}
    return body


def build_error(code, request_id):
    if isinstance(code, str):
        try:
            code = ErrorCode(code)
        except ValueError:
            raise ValueError("unknown error code") from None
    return http_error(code, request_id)
