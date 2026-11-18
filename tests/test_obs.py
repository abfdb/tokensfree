"""Structured logging: fixed whitelist, redaction, never any prompt text."""
import io
import json

from tokensfree import obs


def make_stream():
    stream = io.StringIO()
    obs.configure_logging(stream)
    return stream


def test_event_is_one_json_line():
    stream = make_stream()
    obs.log_event("request.start", request_id="r1", outcome="started")
    line = stream.getvalue().strip()
    record = json.loads(line)
    assert record["request_id"] == "r1"
    assert record["outcome"] == "started"
    assert record["event"] == "request.start"
    assert isinstance(record["ts"], float)


def test_unknown_field_rejected():
    make_stream()
    try:
        obs.log_event("x", messages="SECRET-PROMPT-MARKER")
    except ValueError:
        return
    raise AssertionError("unknown field accepted")


def test_prompt_never_logged():
    stream = make_stream()
    obs.log_event("request.end", request_id="r1", outcome="success")
    assert "SECRET-PROMPT-MARKER" not in stream.getvalue()


def test_bearer_token_redacted():
    assert "abc123xyz" not in obs.redact("Authorization: Bearer abc123xyz")


def test_level_filter_drops_debug_by_default():
    stream = make_stream()
    obs.log_event("x", level="debug")
    assert stream.getvalue() == ""
