"""Parse an upstream OpenAI-compatible SSE stream.

Tolerant of comments and blank lines, strict about payloads: a malformed frame
becomes a non-retryable invalid_response error instead of being skipped, because
silently dropping a frame would truncate the answer.
"""
import json
from dataclasses import dataclass

from tokensfree.adapters.base import NormalizedError

MAX_LINE_CHARS = 65536


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    delta: dict | None = None
    finish_reason: str | None = None
    usage: dict | None = None


def _invalid():
    raise NormalizedError("invalid_response", retryable=False)


def parse_chunk(data):
    if not isinstance(data, dict):
        _invalid()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
    choices = data.get("choices")
    if choices is None:
        if usage is not None:
            return StreamEvent("delta", delta={}, usage=usage)
        _invalid()
    if not isinstance(choices, list):
        _invalid()
    if not choices:
        return StreamEvent("delta", delta={}, usage=usage)
    if len(choices) != 1:
        _invalid()
    choice = choices[0]
    if not isinstance(choice, dict):
        _invalid()
    delta = choice.get("delta")
    if delta is not None and not isinstance(delta, dict):
        _invalid()
    finish_reason = choice.get("finish_reason")
    return StreamEvent(
        "finish" if finish_reason is not None else "delta",
        delta=delta or {},
        finish_reason=finish_reason,
        usage=usage,
    )


def iter_sse_events(text):
    """Yield StreamEvent for each data frame; stop after [DONE]."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if len(payload) > MAX_LINE_CHARS:
            _invalid()
        if payload == "[DONE]":
            yield StreamEvent("done")
            return
        try:
            data = json.loads(payload)
        except (ValueError, RecursionError):
            _invalid()
        yield parse_chunk(data)
