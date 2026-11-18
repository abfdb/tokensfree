"""Provider adapter contract and upstream error normalization.

Two vocabularies stay separate: tokensfree ErrorCode describes what a client sees,
NormalizedError describes what an upstream did. Adapters only ever produce the latter.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol

SUPPORTED_CAPABILITIES = frozenset({"text"})

# Sentinel that makes Admission unconstructable outside the admission gate.
_ADMISSION_TOKEN = object()


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple
    max_tokens: int = 1024
    stream: bool = False
    tools: tuple = ()
    tool_choice: Any = None
    temperature: float | None = None
    top_p: float | None = None
    stop: tuple = ()
    seed: int | None = None
    response_format: dict | None = None
    stream_include_usage: bool = False
    presence_penalty: float | None = None
    frequency_penalty: float | None = None


@dataclass(frozen=True)
class ChatResult:
    content: str
    finish_reason: str = "stop"
    model: str = ""
    usage: dict | None = None
    tool_calls: tuple = ()


class NormalizedError(Exception):
    """Upstream failure with a stable code. Never carries upstream response text."""

    __slots__ = ("code", "retryable", "provider", "retry_after_s")

    def __init__(self, code, *, retryable, provider="", retry_after_s=None):
        self.code = str(code)
        self.retryable = bool(retryable)
        self.provider = str(provider)
        self.retry_after_s = retry_after_s
        super().__init__(self.code)

    def __str__(self):
        return self.code


def classify_status(status, retry_after_s=None):
    """Map an upstream HTTP status to a NormalizedError, or None for success."""
    if type(status) is not int or not 100 <= status <= 599:
        raise TypeError("status must be an int in 100..599")
    if retry_after_s is not None:
        if type(retry_after_s) not in (int, float) or retry_after_s < 0:
            raise ValueError("invalid retry_after_s")
    if 200 <= status < 300:
        return None
    if status in (401, 403):
        return NormalizedError("auth_invalid", retryable=False)
    if status == 402:
        return NormalizedError("billing_required", retryable=False)
    if 300 <= status < 400:
        return NormalizedError("redirect_rejected", retryable=False)
    if status == 429:
        if retry_after_s is not None and retry_after_s <= 60:
            return NormalizedError("rate_limited", retryable=True, retry_after_s=retry_after_s)
        return NormalizedError("quota_exhausted", retryable=False)
    if 500 <= status < 600:
        return NormalizedError("upstream_error", retryable=True)
    return NormalizedError("invalid_request_upstream", retryable=False)


@dataclass(frozen=True)
class Admission:
    """Proof that the router admitted this provider for one attempt.

    The constructor is private by sentinel: direct construction raises TypeError.
    This prevents accidental bypass, not a hostile in-process attacker.
    """

    provider: str
    capabilities: frozenset
    simulation: bool
    _token: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._token is not _ADMISSION_TOKEN:
            raise TypeError("Admission must be issued by tokensfree.router.admission")


class ChatAdapter(Protocol):
    name: str
    capabilities: frozenset

    async def complete(self, request: ChatRequest, admission: Admission) -> ChatResult:
        ...
