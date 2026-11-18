"""Structured logs with a fixed field whitelist.

Prompts, messages and credentials never enter this module: callers can only pass
whitelisted scalar fields, and the stream receives one JSON object per line.
"""
import json
import re
import sys
import time

ALLOWED_FIELDS = frozenset({
    "event", "request_id", "client_id", "provider", "account_id", "outcome",
    "latency_ms", "error_code", "bytes_in", "bytes_out", "level",
})

_LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_stream = sys.stderr
_level = "info"
_TOKEN = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]{6,}", re.IGNORECASE)


def configure_logging(stream=None, *, level="info"):
    global _stream, _level
    if level not in _LEVELS:
        raise ValueError("invalid log level")
    _stream = sys.stderr if stream is None else stream
    _level = level


def redact(text):
    return _TOKEN.sub(r"\1<redacted>", str(text))


def log_event(event, *, level="info", **fields):
    if not isinstance(event, str) or not event:
        raise ValueError("invalid event name")
    if level not in _LEVELS:
        raise ValueError("invalid log level")
    unknown = set(fields) - ALLOWED_FIELDS
    if unknown:
        raise ValueError("unknown log field: " + ",".join(sorted(unknown)))
    if _LEVELS[level] < _LEVELS[_level]:
        return
    record = {"ts": round(time.time(), 3), "level": level, "event": event}
    record.update(fields)
    _stream.write(redact(json.dumps(record, ensure_ascii=False)) + "\n")
    _stream.flush()
