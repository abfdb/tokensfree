"""Error contract: stable codes, fixed messages, no free-text echo."""
import pytest

from tokensfree.errors import DEFAULT_MESSAGES, ERROR_TABLE, ErrorCode, error_body, http_error


def test_every_code_has_status_and_retryable():
    for code in ErrorCode:
        status, retryable = ERROR_TABLE[code]
        assert 400 <= status < 600
        assert isinstance(retryable, bool)


def test_every_code_has_a_fixed_message():
    for code in ErrorCode:
        assert code in DEFAULT_MESSAGES
        assert DEFAULT_MESSAGES[code]


def test_body_shape_is_fixed():
    body = error_body(ErrorCode.invalid_request, 'req-1')
    assert set(body) == {'error'}
    assert set(body['error']) == {'code', 'message', 'retryable', 'request_id'}
    assert body['error']['code'] == 'invalid_request'
    assert body['error']['request_id'] == 'req-1'


def test_http_error_status_matches_table():
    status, body = http_error(ErrorCode.no_free_provider, 'req-2')
    assert status == 503
    assert body['error']['retryable'] is True


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        error_body('not-a-code', 'req-3')


def test_error_body_has_no_free_text_override():
    # 唯一能让请求内容进入错误体的入口必须不存在（Review Focus #1）
    with pytest.raises(TypeError):
        error_body(ErrorCode.invalid_request, 'req-4', message='SECRET-PROMPT-MARKER')
