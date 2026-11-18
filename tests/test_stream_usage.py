"""stream_options.include_usage: a usage-only frame after the finish frame."""
import asyncio
import json

import pytest

from providers import ProviderSpec
from tokensfree.adapters.stream_parse import StreamEvent
from tokensfree.config import load_config
from tokensfree.edge.app_core import Services
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.router.admission import admit

CONFIG = load_config({})
STREAMING = frozenset({"text", "stream"})
ALPHA = ProviderSpec("alpha", "m-a", "mock_zero_cost", 200, STREAMING)
BASE_PAYLOAD = {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}], "stream": True}


class FakeStreamAdapter:
    capabilities = STREAMING

    def __init__(self, events):
        self.events = list(events)

    async def stream(self, request, admission):
        for event in self.events:
            yield event


def collect(events, *, include_usage):
    services = Services(
        specs=(ALPHA,),
        adapters={"alpha": FakeStreamAdapter(events)},
        admit=lambda spec: admit(spec, CONFIG, frozenset({"alpha"}), 100),
        authorized=frozenset({"alpha"}),
    )
    payload = dict(BASE_PAYLOAD)
    if include_usage:
        payload["stream_options"] = {"include_usage": True}
    plan = prepare_stream(payload, services=services, request_id="req-usage")
    frames = []

    async def run_all():
        async for frame in iter_frames(plan, admit=services.admit):
            frames.append(frame)

    asyncio.run(run_all())
    return frames


def frames_of(frames):
    parsed = []
    for frame in frames:
        text = frame.decode("utf-8").strip()
        if text.startswith("data: {"):
            parsed.append(("data", json.loads(text[len("data: "):])))
        elif text == "data: [DONE]":
            parsed.append(("done", None))
        else:
            parsed.append(("other", text))
    return parsed


def test_usage_frame_follows_finish_when_requested():
    usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    frames = collect([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="stop", usage=usage)], include_usage=True)
    parsed = frames_of(frames)
    assert parsed[-1] == ("done", None)
    usage_frames = [payload for kind, payload in parsed if kind == "data" and payload.get("choices") == []]
    assert len(usage_frames) == 1
    assert usage_frames[0]["usage"] == usage
    finish_frames = [payload for kind, payload in parsed if kind == "data" and payload.get("choices")]
    assert finish_frames[-1]["choices"][0]["finish_reason"] == "stop"


def test_no_usage_frame_when_not_requested():
    usage = {"total_tokens": 5}
    frames = collect([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="stop", usage=usage)], include_usage=False)
    parsed = frames_of(frames)
    assert not any(kind == "data" and payload.get("choices") == [] for kind, payload in parsed)


def test_usage_only_frame_after_finish_is_captured():
    usage = {"total_tokens": 9}
    events = [
        StreamEvent("delta", {"content": "ok"}),
        StreamEvent("finish", finish_reason="stop"),
        StreamEvent("delta", {}, usage=usage),
        StreamEvent("done"),
    ]
    parsed = frames_of(collect(events, include_usage=True))
    usage_frames = [payload for kind, payload in parsed if kind == "data" and payload.get("choices") == []]
    assert len(usage_frames) == 1 and usage_frames[0]["usage"] == usage
    assert parsed[-1] == ("done", None)


def test_finish_without_done_still_delivers_usage_and_done():
    usage = {"total_tokens": 4}
    parsed = frames_of(collect([StreamEvent("delta", {"content": "ok"}), StreamEvent("finish", finish_reason="length", usage=usage)], include_usage=True))
    assert parsed[-1] == ("done", None)
    assert any(kind == "data" and payload.get("choices") == [] and payload.get("usage") == usage for kind, payload in parsed)


@pytest.mark.parametrize(
    "payload",
    [
        {"stream_options": "yes"},
        {"stream_options": {"include_usage": "yes"}},
        {"stream_options": {"include_usage": 1}},
    ],
)
def test_invalid_stream_options_are_rejected(payload):
    from tokensfree.edge.protocol import RequestRejected, parse_chat_payload

    merged = dict(BASE_PAYLOAD)
    merged.update(payload)
    with pytest.raises(RequestRejected):
        parse_chat_payload(merged)


def test_include_usage_requires_stream():
    from tokensfree.edge.protocol import RequestRejected, parse_chat_payload

    with pytest.raises(RequestRejected):
        parse_chat_payload({"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}],
                           "stream_options": {"include_usage": True}})
