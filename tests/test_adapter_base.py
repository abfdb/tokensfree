"""Adapter contract: request/result types, upstream error normalization, opaque admission."""
import pytest

from tokensfree.adapters.base import (
    Admission,
    ChatRequest,
    ChatResult,
    NormalizedError,
    classify_status,
)


def test_success_status_has_no_error():
    assert classify_status(200) is None
    assert classify_status(204) is None


def test_normalization_table():
    cases = [
        (401, None, "auth_invalid", False),
        (403, None, "auth_invalid", False),
        (402, None, "billing_required", False),
        (302, None, "redirect_rejected", False),
        (500, None, "upstream_error", True),
        (503, None, "upstream_error", True),
        (429, 5.0, "rate_limited", True),
        (429, 60.0, "rate_limited", True),
        (429, 61.0, "quota_exhausted", False),
        (429, None, "quota_exhausted", False),
        (404, None, "invalid_request_upstream", False),
    ]
    for status, retry_after, code, retryable in cases:
        error = classify_status(status, retry_after)
        assert error is not None
        assert error.code == code
        assert error.retryable is retryable


def test_invalid_status_rejected():
    for status in (True, "200", None, 99):
        with pytest.raises((TypeError, ValueError)):
            classify_status(status)


def test_error_str_has_no_upstream_text():
    error = NormalizedError("rate_limited", retryable=True, provider="mock-alpha", retry_after_s=3)
    assert str(error) == "rate_limited"
    assert "mock-alpha" not in str(error)


def test_admission_cannot_be_constructed_directly():
    with pytest.raises(TypeError):
        Admission(provider="mock-alpha", capabilities=frozenset({"text"}), simulation=True)


def test_request_defaults_are_frozen():
    request = ChatRequest(model="tokensfree-auto", messages=({"role": "user", "content": "x"},))
    assert request.stream is False
    assert request.tools == ()
    with pytest.raises(Exception):
        request.model = "other"
    result = ChatResult(content="hi")
    assert result.finish_reason == "stop"
