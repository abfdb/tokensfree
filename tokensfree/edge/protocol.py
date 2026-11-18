"""Framework-free edge protocol.

Turns an OpenAI-shaped chat payload into the kernel's ChatRequest, and turns
kernel results/errors back into OpenAI-shaped bodies. No HTTP objects live here,
so the legacy stdlib server and the future async backend share one semantic.
"""
from dataclasses import dataclass

from tokensfree.adapters.base import ChatRequest, NormalizedError
from tokensfree.errors import ErrorCode, http_error

ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool", "developer"})
DEFAULT_MAX_TOKENS = 1024

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
        if role not in ALLOWED_ROLES:
            _reject()
        if not isinstance(content, str) or len(content) > max_content_chars:
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
            if role != "assistant" or not isinstance(tool_calls, list):
                _reject()
            entry["tool_calls"] = tool_calls
            needs_tools = True
        normalized.append(entry)
    return tuple(normalized), needs_tools


def parse_chat_payload(payload, *, developer_role_policy="map", max_messages=32, max_content_chars=4000):
    if not isinstance(payload, dict):
        _reject()
    model = payload.get("model")
    if not isinstance(model, str) or not 1 <= len(model) <= 128:
        _reject()
    stream = payload.get("stream", False)
    if not isinstance(stream, bool):
        _reject()
    max_tokens = payload.get("max_tokens", DEFAULT_MAX_TOKENS)
    if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
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
    tool_choice = payload.get("tool_choice")
    if tool_choice is not None and not isinstance(tool_choice, (str, dict)):
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
    )
    return ParsedChat(request=request, stream=stream, requires_tools=requires_tools, capabilities=frozenset(capabilities))


def map_upstream_error(error):
    if not isinstance(error, NormalizedError):
        raise TypeError("map_upstream_error requires a NormalizedError")
    return _UPSTREAM_TO_CLIENT.get(error.code, ErrorCode.upstream_error)


def build_chat_response(*, request_id, model, content, usage=None, finish_reason="stop", trace=None):
    body = {
        "id": request_id,
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}
        ],
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
