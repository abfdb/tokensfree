"""Enabling a real provider is an environment change, not a code change."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.edge.stream_core import iter_frames, prepare_stream
from tokensfree.runtime import REAL_ENV_KEYS, build_real_runtime, real_spec_from_env
from upstream_support import JsonUpstream, SSEUpstream

TOKEN = "r" * 32
KEY = "sk-local-test-only"
MODEL = "real-model"
PROVIDER = "zhipu-test"
CONFIG_ON = load_config({"TOKENSFREE_ENABLE_REAL": "1", "TOKENSFREE_LOCAL_TOKEN": TOKEN})
CONFIG_OFF = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
PAYLOAD = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
SUCCESS = json.dumps(
    {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "from the fixture upstream"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
)
SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"real streaming"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def env_for(port, *, evidence=2000.0, **overrides):
    values = {
        "TOKENSFREE_REAL_PROVIDER": PROVIDER,
        "TOKENSFREE_REAL_BASE_URL": "http://127.0.0.1:" + str(port) + "/v1",
        "TOKENSFREE_REAL_API_KEY": KEY,
        "TOKENSFREE_REAL_MODEL": MODEL,
        "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT": str(evidence),
    }
    values.update(overrides)
    return values


@pytest.fixture()
def json_upstream():
    server = JsonUpstream(SUCCESS)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def runtime_for(upstream_port, config, clock=lambda: 1000.0):
    return build_real_runtime(config=config, env=env_for(upstream_port), clock=clock)


def chat(runtime):
    return asyncio.run(handle_chat(PAYLOAD, services=runtime.services, request_id="req-real"))


def test_missing_environment_is_reported_without_the_key():
    with pytest.raises(ValueError) as caught:
        real_spec_from_env({}, clock=lambda: 1000.0)
    message = str(caught.value)
    for key in REAL_ENV_KEYS:
        assert key in message
    assert KEY not in message


def test_expired_evidence_refuses_to_build():
    with pytest.raises(ValueError) as caught:
        real_spec_from_env(env_for(1, evidence=500.0), clock=lambda: 1000.0)
    assert "past" in str(caught.value)


def test_gate_open_calls_the_real_adapter_against_the_fixture(json_upstream):
    runtime = runtime_for(json_upstream.port, CONFIG_ON)
    outcome = chat(runtime)
    assert outcome.status == 200
    assert outcome.body["choices"][0]["message"]["content"] == "from the fixture upstream"
    assert outcome.provider == PROVIDER
    sent = json_upstream.requests[-1]
    assert sent["model"] == MODEL
    assert json_upstream.headers["Authorization"] == "Bearer " + KEY


def test_gate_closed_never_contacts_the_upstream(json_upstream):
    runtime = runtime_for(json_upstream.port, CONFIG_OFF)
    outcome = chat(runtime)
    assert outcome.status == 503
    assert outcome.body["error"]["code"] == "no_free_provider"
    assert json_upstream.requests == []


def test_streaming_through_the_real_runtime():
    server = SSEUpstream(SSE)
    server.start()
    try:
        runtime = runtime_for(server.port, CONFIG_ON)
        plan = prepare_stream(dict(PAYLOAD, stream=True), services=runtime.services, request_id="req-real-stream")
        frames = []

        async def run_all():
            async for frame in iter_frames(plan, admit=runtime.services.admit, breaker_for=None):
                frames.append(frame)

        asyncio.run(run_all())
        joined = b"".join(frames).decode("utf-8")
        assert "real streaming" in joined
        assert joined.strip().endswith("data: [DONE]")
    finally:
        server.stop()


def test_status_reports_a_real_provider(json_upstream):
    from tokensfree.edge.backend import EdgeRequest, MemoryBackend

    runtime = runtime_for(json_upstream.port, CONFIG_ON)
    backend = MemoryBackend(config=CONFIG_ON, services=runtime.services, port=8765, token=TOKEN)
    response = asyncio.run(
        backend.handle(
            EdgeRequest(
                method="GET",
                path="/api/status",
                headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN},
            )
        )
    )
    payload = json.loads(response.body)
    assert payload["simulation"] is False
    assert payload["providers"][0]["state"] == "eligible"


def test_host_real_mode_refuses_incomplete_environment(tmp_path):
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = TOKEN
    for key in REAL_ENV_KEYS:
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "tokensfree", "--real", "--port", "0"],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        env=env,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "TOKENSFREE_REAL_BASE_URL" in output
    assert KEY not in output
