"""Startup configuration.

Read once from an explicit mapping supplied by the entry point. This module never
touches os.environ implicitly, so no request path can change configuration.
"""
import math
import re
from dataclasses import dataclass
from typing import Mapping

DEFAULT_PORT = 8765
DEFAULT_MAX_BODY_BYTES = 262144
DEFAULT_TIMEOUT_S = 60.0

_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,128}")
_TRUE = "1"
_FALSE = "0"


@dataclass(frozen=True)
class AppConfig:
    port: int = DEFAULT_PORT
    local_token: str | None = None
    enable_real: bool = False
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    default_timeout_s: float = DEFAULT_TIMEOUT_S


def _parse_int(env, name, default, low, high):
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = int(raw, 10)
    except (TypeError, ValueError):
        raise ValueError("invalid integer for " + name) from None
    if not low <= value <= high:
        raise ValueError("out of range for " + name)
    return value


def _parse_float(env, name, default, low, high):
    raw = env.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError("invalid number for " + name) from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("out of range for " + name)
    return value


def load_config(env: Mapping[str, str]) -> AppConfig:
    if not hasattr(env, "get") or not callable(env.get):
        raise TypeError("env must be a mapping")

    raw_enable = env.get("TOKENSFREE_ENABLE_REAL")
    if raw_enable is None:
        enable_real = False
    elif raw_enable in (_TRUE, _FALSE):
        enable_real = raw_enable == _TRUE
    else:
        raise ValueError("TOKENSFREE_ENABLE_REAL must be 0 or 1")

    token = env.get("TOKENSFREE_LOCAL_TOKEN")
    if token is not None:
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
            raise ValueError("TOKENSFREE_LOCAL_TOKEN must be 32-128 [A-Za-z0-9_-] characters")

    return AppConfig(
        port=_parse_int(env, "TOKENSFREE_PORT", DEFAULT_PORT, 1, 65535),
        local_token=token,
        enable_real=enable_real,
        max_body_bytes=_parse_int(env, "TOKENSFREE_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES, 1024, 1048576),
        default_timeout_s=_parse_float(env, "TOKENSFREE_TIMEOUT_S", DEFAULT_TIMEOUT_S, 1.0, 600.0),
    )
