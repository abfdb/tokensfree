"""Stable error contract shared by the legacy mock server and the future gateway.

Two rules make this module the only way an error reaches a client:
1. Every response carries a fixed code, a fixed message and a request id.
2. There is no free-text override, so request content can never be echoed back
   (see Review Focus #1 in docs/superpowers/plans/).
"""
from enum import Enum


class ErrorCode(str, Enum):
    invalid_request = "invalid_request"
    unsupported_capability = "unsupported_capability"
    unauthorized = "unauthorized"
    origin_rejected = "origin_rejected"
    not_found = "not_found"
    model_not_found = "model_not_found"
    policy_blocked = "policy_blocked"
    free_status_unverified = "free_status_unverified"
    no_free_provider = "no_free_provider"
    idempotency_conflict = "idempotency_conflict"
    idempotency_capacity = "idempotency_capacity"
    refusal = "refusal"
    payload_too_large = "payload_too_large"
    method_not_allowed = "method_not_allowed"
    upstream_auth_failed = "upstream_auth_failed"
    upstream_rate_limited = "upstream_rate_limited"
    upstream_error = "upstream_error"
    upstream_timeout = "upstream_timeout"
    upstream_disconnected = "upstream_disconnected"
    internal_error = "internal_error"


# code -> (http_status, retryable)
ERROR_TABLE = {
    ErrorCode.invalid_request: (400, False),
    ErrorCode.unsupported_capability: (400, False),
    ErrorCode.unauthorized: (401, False),
    ErrorCode.origin_rejected: (403, False),
    ErrorCode.not_found: (404, False),
    ErrorCode.model_not_found: (404, False),
    ErrorCode.policy_blocked: (403, False),
    ErrorCode.free_status_unverified: (403, False),
    ErrorCode.no_free_provider: (503, True),
    ErrorCode.idempotency_conflict: (409, False),
    ErrorCode.idempotency_capacity: (503, True),
    ErrorCode.refusal: (422, False),
    ErrorCode.payload_too_large: (413, False),
    ErrorCode.method_not_allowed: (405, False),
    ErrorCode.upstream_auth_failed: (502, False),
    ErrorCode.upstream_rate_limited: (429, True),
    ErrorCode.upstream_error: (502, True),
    ErrorCode.upstream_timeout: (504, True),
    ErrorCode.upstream_disconnected: (502, False),
    ErrorCode.internal_error: (500, True),
}

DEFAULT_MESSAGES = {
    ErrorCode.invalid_request: "请求形状非法或包含不支持的能力。",
    ErrorCode.unsupported_capability: "当前版本未实现请求所需的能力（流式/工具/多模态）。",
    ErrorCode.unauthorized: "本地会话令牌无效。",
    ErrorCode.origin_rejected: "Host 或 Origin 被拒绝。",
    ErrorCode.not_found: "资源不存在。",
    ErrorCode.model_not_found: "模型不可路由。",
    ErrorCode.policy_blocked: "策略阻止该请求。",
    ErrorCode.free_status_unverified: "免费资格未验证。",
    ErrorCode.no_free_provider: "没有可用的免费供应商。已停止，不回退付费。",
    ErrorCode.idempotency_conflict: "同一请求键不能用于不同内容，请为新请求生成新键。",
    ErrorCode.idempotency_capacity: "去重缓存已满，请等待旧记录到期。请求未执行。",
    ErrorCode.refusal: "上游安全拒绝：停止，不通过切换规避。",
    ErrorCode.payload_too_large: "请求体超出允许的大小。",
    ErrorCode.method_not_allowed: "该路径不支持此 HTTP 方法。",
    ErrorCode.upstream_auth_failed: "上游凭证失效。",
    ErrorCode.upstream_rate_limited: "上游限流，请稍后重试。",
    ErrorCode.upstream_error: "上游服务错误。",
    ErrorCode.upstream_timeout: "上游响应超时。",
    ErrorCode.upstream_disconnected: "上游流在传输中断开。",
    ErrorCode.internal_error: "内部错误。",
}


def error_body(code, request_id):
    """Build the only legal error body. No free-text parameter exists on purpose."""
    if not isinstance(code, ErrorCode):
        raise ValueError("unknown error code")
    status, retryable = ERROR_TABLE[code]
    return {
        "error": {
            "code": code.value,
            "message": DEFAULT_MESSAGES[code],
            "retryable": retryable,
            "request_id": str(request_id),
        }
    }


def http_error(code, request_id):
    """Convenience for handlers: (http_status, body)."""
    return ERROR_TABLE[code][0], error_body(code, request_id)
