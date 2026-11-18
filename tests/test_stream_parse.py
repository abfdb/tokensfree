"""Upstream SSE parsing: tolerate noise, reject malformed frames, stop at [DONE]."""
import pytest

from tokensfree.adapters.base import NormalizedError
from tokensfree.adapters.stream_parse import StreamEvent, iter_sse_events


def sse(*payloads):
    return "".join("data: " + payload + "\n\n" for payload in payloads)


def collect(text):
    return list(iter_sse_events(text))


def test_parses_delta_and_finish():
    text = sse(
        '{"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
        '{"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":null}]}',
        '{"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
        "[DONE]",
    )
    events = collect(text)
    assert [event.kind for event in events] == ["delta", "delta", "finish", "done"]
    assert events[0].delta == {"role": "assistant"}
    assert events[1].delta == {"content": "hi"}
    assert events[2].finish_reason == "stop"


def test_ignores_comments_and_blank_lines():
    text = ": keep-alive\n\n\ndata: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"x\"},\"finish_reason\":null}]}\n\n"
    events = collect(text)
    assert len(events) == 1
    assert events[0].delta == {"content": "x"}


def test_usage_only_chunk_is_kept():
    text = sse('{"choices":[],"usage":{"total_tokens":3}}', "[DONE]")
    events = collect(text)
    assert events[0].kind == "delta"
    assert events[0].usage == {"total_tokens": 3}


def test_malformed_json_raises_normalized_error():
    with pytest.raises(NormalizedError) as caught:
        collect(sse("{not json"))
    assert caught.value.code == "invalid_response"
    assert caught.value.retryable is False


def test_non_object_payload_rejected():
    for bad in ("[]", "\"text\"", "null"):
        with pytest.raises(NormalizedError):
            collect(sse(bad))


def test_stream_without_choices_is_rejected():
    with pytest.raises(NormalizedError):
        collect(sse('{"id":"x"}'))


def test_oversized_line_rejected():
    with pytest.raises(NormalizedError):
        collect("data: " + "x" * 70000 + "\n\n")


def test_event_dataclass_is_frozen():
    event = StreamEvent("delta", {"content": "x"})
    with pytest.raises(Exception):
        event.kind = "finish"
