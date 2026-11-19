"""Multi-channel runtime from provider profiles, plus the host --profiles path."""
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tokensfree import profiles as profiles_module
from tokensfree.adapters.base import Admission
from tokensfree.config import load_config
from tokensfree.edge.app_core import handle_chat
from tokensfree.edge.backend import EdgeRequest, MemoryBackend
from tokensfree.runtime import Storage, build_profiles_runtime
from upstream_support import JsonUpstream

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "p" * 32
OK_BODY = json.dumps(
    {
        "model": "local-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "local ok"}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 2},
    }
)
CONFIG = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN, "TOKENSFREE_ENABLE_REAL": "1"})


def local_profile(port, **overrides):
    values = {
        "id": "local-ollama",
        "name": "本机 Ollama",
        "base_url": "http://127.0.0.1:%d/v1" % port,
        "model": "local-model",
        "cost_policy": "zero_cost_local",
        "capabilities": frozenset({"text"}),
    }
    values.update(overrides)
    return profiles_module.validate_profile(profiles_module.Profile(**values))


def free_profile(**overrides):
    values = {
        "id": "zhipu-free",
        "name": "智谱免费额度",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4.7-flash",
        "cost_policy": "free_verified",
        "capabilities": frozenset({"text", "stream", "tools"}),
        "evidence": {"expires_at": 2_000_000_000},
    }
    values.update(overrides)
    return profiles_module.validate_profile(profiles_module.Profile(**values))


def paid_profile():
    return profiles_module.validate_profile(
        profiles_module.Profile(
            id="paid-remote",
            name="自费远端",
            base_url="https://api.example.invalid/v1",
            model="paid-model",
            cost_policy="user_paid",
            capabilities=frozenset({"text"}),
        )
    )


def loader(profile_id):
    return "key-" + profile_id


@pytest.fixture()
def upstream():
    server = JsonUpstream(OK_BODY)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_local_profile_serves_chat(upstream):
    runtime = build_profiles_runtime([local_profile(upstream.port)], config=CONFIG, key_loader=loader)
    assert [spec.name for spec in runtime.services.specs] == ["local-ollama"]
    outcome = asyncio.run(
        handle_chat(
            {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]},
            services=runtime.services,
            request_id="req-profile",
        )
    )
    assert outcome.status == 200
    assert outcome.body["choices"][0]["message"]["content"] == "local ok"
    assert outcome.provider == "local-ollama"


def test_free_profile_requires_valid_evidence(upstream):
    profile = free_profile()
    runtime = build_profiles_runtime([profile], config=CONFIG, key_loader=loader)
    styles = [spec.cost_evidence for spec in runtime.services.specs]
    assert styles == ["free_verified"]

    expired = free_profile(evidence={"expires_at": 1})
    with pytest.raises(profiles_module.ProfileError):
        build_profiles_runtime([expired], config=CONFIG, key_loader=loader)


def test_missing_key_fails_closed_for_cloud_profiles(upstream):
    profile = free_profile()
    with pytest.raises(ValueError):
        build_profiles_runtime([profile], config=CONFIG, key_loader=lambda profile_id: None)


def test_real_disabled_blocks_every_channel(upstream):
    runtime = build_profiles_runtime([local_profile(upstream.port)], config=load_config({}), key_loader=loader)
    outcome = asyncio.run(
        handle_chat(
            {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]},
            services=runtime.services,
            request_id="req-disabled",
        )
    )
    assert outcome.status == 503
    assert outcome.body["error"]["code"] == "no_free_provider"
    assert "denied:real_disabled" in json.dumps(outcome.body)


def test_user_paid_is_blocked_unless_explicitly_allowed(upstream):
    runtime = build_profiles_runtime([paid_profile()], config=CONFIG, key_loader=loader)
    spec = runtime.services.specs[0]
    with pytest.raises(Exception) as denied:
        runtime.services.admit(spec)
    assert "user_paid_disabled" in str(getattr(denied.value, "reason", denied.value))

    allowed_config = load_config(
        {"TOKENSFREE_LOCAL_TOKEN": TOKEN, "TOKENSFREE_ENABLE_REAL": "1", "TOKENSFREE_ALLOW_USER_PAID": "1"}
    )
    allowed = build_profiles_runtime([paid_profile()], config=allowed_config, key_loader=loader)
    assert isinstance(allowed.services.admit(allowed.services.specs[0]), Admission)


def test_disabled_profiles_are_skipped_and_all_disabled_fails(upstream):
    runtime = build_profiles_runtime(
        [local_profile(upstream.port, enabled=False), local_profile(upstream.port, id="second", name="Second")],
        config=CONFIG,
        key_loader=loader,
    )
    assert [spec.name for spec in runtime.services.specs] == ["second"]
    with pytest.raises(ValueError):
        build_profiles_runtime([local_profile(upstream.port, enabled=False)], config=CONFIG, key_loader=loader)


def test_status_reports_profiles_with_policy(upstream, tmp_path):
    storage = Storage.open(str(tmp_path / "state.db"))
    try:
        runtime = build_profiles_runtime([local_profile(upstream.port)], config=CONFIG, key_loader=loader, storage=storage)
        backend = MemoryBackend(config=CONFIG, services=runtime.services, port=8765, token=TOKEN)
        request = EdgeRequest(
            method="GET",
            path="/api/status",
            headers={"host": "127.0.0.1:8765", "authorization": "Bearer " + TOKEN},
        )
        response = asyncio.run(backend.handle(request))
        payload = json.loads(response.body)
        assert payload["simulation"] is False
        provider = payload["providers"][0]
        assert provider["name"] == "local-ollama"
        assert provider["cost_policy"] == "zero_cost_local"
        assert payload["accounts"][0]["provider"] == "local-ollama"
    finally:
        storage.close()


def test_host_profiles_path_serves_models_and_chat(upstream, tmp_path):
    profiles_path = tmp_path / "providers.json"
    profiles_module.save_profiles([local_profile(upstream.port)], profiles_path)
    env = dict(os.environ)
    env.update(
        {
            "TOKENSFREE_LOCAL_TOKEN": TOKEN,
            "TOKENSFREE_ENABLE_REAL": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "tokensfree", "--profiles", str(profiles_path), "--port", "0", "--db", str(tmp_path / "g.db")],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    port = None
    try:
        deadline = time.time() + 25
        while time.time() < deadline:
            line = process.stdout.readline()
            if not line:
                break
            match = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if match:
                port = int(match.group(1))
                break
        assert port, "gateway did not announce its port"
        base = "http://127.0.0.1:" + str(port)
        request = urllib.request.Request(
            base + "/v1/models", headers={"Authorization": "Bearer " + TOKEN}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            models = json.loads(response.read())
        assert [model["id"] for model in models["data"]] == ["local-model"]

        payload = json.dumps(
            {"model": "tokensfree-auto", "messages": [{"role": "user", "content": "hi"}]}
        ).encode()
        chat = urllib.request.Request(
            base + "/v1/chat/completions",
            data=payload,
            method="POST",
            headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(chat, timeout=30) as response:
            body = json.loads(response.read())
        assert body["choices"][0]["message"]["content"] == "local ok"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
