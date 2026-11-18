"""SSE framing and tool-call delta assembly. No HTTP framework involved."""
import json

import pytest

from tokensfree.sse import DONE, SseEncoder, ToolCallAssembler, keepalive


def frame_bytes(frame):
    assert frame.endswith(b"\n\n")
    return frame


def test_role_and_content_frames_are_openai_shaped():
    encoder = SseEncoder(model="tokensfree-auto", request_id="req-1")
    role = frame_bytes(encoder.chunk({"role": "assistant"}))
    payload = json.loads(role[len(b"data: "):])
    assert payload["object"] == "chat.completion.chunk"
    assert payload["choices"][0]["delta"] == {"role": "assistant"}
    content = json.loads(encoder.chunk({"content": "hi"})[len(b"data: "):])
    assert content["choices"][0]["delta"] == {"content": "hi"}


def test_finish_then_done_and_no_more_chunks():
    encoder = SseEncoder(request_id="req-1")
    encoder.chunk({"role": "assistant"})
    finish = json.loads(encoder.chunk({}, finish_reason="stop")[len(b"data: "):])
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert encoder.done() == DONE
    with pytest.raises(RuntimeError):
        encoder.chunk({"content": "late"})


def test_error_event_is_terminal():
    encoder = SseEncoder(request_id="req-1")
    encoder.chunk({"role": "assistant"})
    frame = encoder.error("upstream_disconnected")
    assert frame.startswith(b"event: error\ndata: ")
    payload = json.loads(frame.split(b"data: ", 1)[1])
    assert payload["error"]["code"] == "upstream_disconnected"
    assert payload["error"]["request_id"] == "req-1"
    with pytest.raises(RuntimeError):
        encoder.done()


def test_keepalive_is_a_comment_frame():
    assert keepalive() == b": keep-alive\n\n"


def test_tool_call_assembly_merges_fragments():
    assembler = ToolCallAssembler()
    assembler.add([{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read"}}])
    assembler.add([{"index": 0, "function": {"arguments": '{"file'}}])
    assembler.add([{"index": 0, "function": {"arguments": '_path": "a.txt"}'}}])
    calls = assembler.finish()
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert json.loads(calls[0]["function"]["arguments"]) == {"file_path": "a.txt"}


def test_tool_call_rejects_changed_id_and_gaps():
    assembler = ToolCallAssembler()
    assembler.add([{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read"}}])
    with pytest.raises(ValueError):
        assembler.add([{"index": 0, "id": "call_2", "function": {"arguments": "{}"}}])
    gap = ToolCallAssembler()
    gap.add([{"index": 1, "id": "call_2", "type": "function", "function": {"name": "read"}}])
    with pytest.raises(ValueError):
        gap.finish()


def test_tool_call_requires_name_before_finish():
    assembler = ToolCallAssembler()
    assembler.add([{"index": 0, "id": "call_1", "type": "function", "function": {"arguments": "{}"}}])
    with pytest.raises(ValueError):
        assembler.finish()
