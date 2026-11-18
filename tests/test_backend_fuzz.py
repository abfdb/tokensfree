"""Backend-level fuzz: hostile payloads must produce a stable HTTP answer, never a 500."""
import asyncio
import json
import random

import pytest

from test_fuzz_inputs import mutate_valid, random_payload
from tokensfree.config import load_config
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.runtime import build_mock_runtime

TOKEN = "f" * 32
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
SERVICES = build_mock_runtime(config=CONFIG).services
BACKEND = MemoryBackend(config=CONFIG, services=SERVICES, port=8765, token=TOKEN)


def chat_request(payload, *, raw=None):
    body = raw if raw is not None else json.dumps(payload).encode()
    return EdgeRequest(
        method="POST",
        path="/v1/chat/completions",
        headers={
            "host": "127.0.0.1:8765",
            "authorization": "Bearer " + TOKEN,
            "content-type": "application/json",
        },
        body=body,
    )


def payloads(count):
    rng = random.Random(424242)
    for _ in range(count):
        yield mutate_valid(rng) if rng.random() < 0.5 else random_payload(rng)


def consume(response):
    async def run():
        frames = []
        if response.stream is not None:
            async for chunk in response.stream:
                frames.append(chunk)
                if len(frames) > 400:
                    raise AssertionError("stream did not terminate")
        return frames

    return asyncio.run(run())


@pytest.mark.parametrize("index", range(4))
def test_random_json_payloads_never_500(index):
    for payload in list(payloads(140))[index * 35:(index + 1) * 35]:
        response = asyncio.run(BACKEND.handle(chat_request(payload)))
        assert 200 <= response.status < 500, (response.status, payload)
        parsed = json.loads(response.body)
        assert isinstance(parsed, dict)


def test_random_raw_bodies_never_500():
    samples = [b"", b"{", b"null", b"[]", b'"text"', b"\xff\xfe", b"[" * 500, b'{"stream": true}']
    for raw in samples:
        response = asyncio.run(BACKEND.handle(chat_request(None, raw=raw)))
        assert 200 <= response.status < 500, (response.status, raw[:20])
        json.loads(response.body)


def test_random_stream_payloads_never_500():
    for payload in list(payloads(60)):
        payload = dict(payload) if isinstance(payload, dict) else {}
        payload["stream"] = True
        response = asyncio.run(BACKEND.handle(chat_request(payload)))
        assert 200 <= response.status < 500
        frames = consume(response)
        if response.stream is not None:
            assert frames, "streaming 200 with no frames"
            assert frames[-1].startswith(b"data:") or b"event:" in frames[-1]
        else:
            parsed = json.loads(response.body)
            assert isinstance(parsed.get("error"), dict)
