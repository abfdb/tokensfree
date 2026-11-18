"""Async loopback adapter over the offline protocol fixture.

Transport is fixed to literal 127.0.0.1 via protocol.LoopbackFixtureAdapter:
no endpoint parameter, no redirects, no proxy, no real credentials.
"""
import asyncio

from protocol import LoopbackFixtureAdapter
from tokensfree.adapters.base import Admission, ChatRequest, ChatResult, NormalizedError

_OUTCOME_ERRORS = {
    "auth": ("auth_invalid", False),
    "limited": ("rate_limited", True),
    "unavailable": ("upstream_error", True),
    "refusal": ("refusal", False),
}


class LoopbackAdapter:
    name = "loopback"
    capabilities = frozenset({"text"})

    def __init__(self, port, timeout=2.0):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid loopback port")
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 < timeout <= 10:
            raise ValueError("invalid timeout")
        self.port = port
        self.timeout = float(timeout)

    async def complete(self, request, admission):
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be a ChatRequest")
        if not isinstance(admission, Admission):
            raise TypeError("admission must be issued by the router")
        fixture = LoopbackFixtureAdapter(self.port, self.timeout)
        messages = [dict(message) for message in request.messages]
        result = await asyncio.to_thread(
            fixture.complete, messages, request.model or "fixture-model", request.max_tokens
        )
        if result.outcome == "success":
            return ChatResult(
                content=result.content,
                finish_reason="length" if result.reason == "truncated" else "stop",
                model=request.model,
            )
        if result.outcome == "blocked":
            if result.reason == "redirect_rejected":
                raise NormalizedError("redirect_rejected", retryable=False, provider=self.name)
            if result.reason == "billing_required":
                raise NormalizedError("billing_required", retryable=False, provider=self.name)
            raise NormalizedError("unsupported_tool_response", retryable=False, provider=self.name)
        code, retryable = _OUTCOME_ERRORS.get(result.outcome, ("upstream_error", True))
        raise NormalizedError(code, retryable=retryable, provider=self.name)
