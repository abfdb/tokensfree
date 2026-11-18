"""Real runtime can read the provider key from the Windows Credential Manager."""
import asyncio
import json
import uuid

import pytest

from tokensfree import credstore
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import build_real_runtime
from upstream_support import JsonUpstream
from test_multi_key import CONFIG_ON, MODEL

pytestmark = pytest.mark.skipif(not credstore.is_supported(), reason="Windows Credential Manager only")

OK_BODY = '{"choices":[{"index":0,"message":{"role":"assistant","content":"from credstore"},"finish_reason":"stop"}],"usage":{"total_tokens":2}}'
KEY = "credstore-key-0123456789"


@pytest.fixture()
def provider():
    name = "tf-rt-" + uuid.uuid4().hex[:12]
    try:
        yield name
    finally:
        try:
            credstore.delete_secret(name)
        except credstore.CredentialError:
            pass


def env_for(upstream_port, provider_name, snapshot_path):
    return {
        "TOKENSFREE_REAL_PROVIDER": provider_name,
        "TOKENSFREE_REAL_BASE_URL": "http://127.0.0.1:%d/v1" % upstream_port,
        "TOKENSFREE_REAL_MODEL": MODEL,
        "TOKENSFREE_REAL_API_KEY_STORE": provider_name,
        "TOKENSFREE_REAL_EVIDENCE_SNAPSHOT": str(snapshot_path),
    }


def write_snapshot(tmp_path, provider_name):
    path = tmp_path / "evidence.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": provider_name,
                "model": MODEL,
                "source": "https://example.invalid/pricing.md",
                "sha256": "c" * 64,
                "evidence_expires_at": 2000,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_runtime_reads_the_key_from_the_credential_store(provider, tmp_path):
    upstream = JsonUpstream(OK_BODY)
    upstream.start()
    try:
        credstore.save_secret(provider, KEY)
        runtime = build_real_runtime(
            config=CONFIG_ON,
            env=env_for(upstream.port, provider, write_snapshot(tmp_path, provider)),
            clock=lambda: 1000.0,
        )
        outcome = asyncio.run(
            handle_chat(
                {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
                services=runtime.services,
                request_id="req-credstore",
            )
        )
        assert outcome.status == 200
        assert outcome.body["choices"][0]["message"]["content"] == "from credstore"
        assert outcome.provider == provider
    finally:
        upstream.stop()


def test_missing_credential_fails_closed(provider, tmp_path):
    env = env_for(1, provider, write_snapshot(tmp_path, provider))
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    message = str(caught.value)
    assert "system credential store" in message
    assert provider in message


def test_unsupported_platform_fails_closed_without_leaking_the_name(monkeypatch, tmp_path):
    monkeypatch.setattr(credstore, "is_supported", lambda: False)
    env = {
        "TOKENSFREE_REAL_PROVIDER": "zhipu",
        "TOKENSFREE_REAL_BASE_URL": "https://example.invalid/v1",
        "TOKENSFREE_REAL_MODEL": MODEL,
        "TOKENSFREE_REAL_API_KEY_STORE": "zhipu",
        "TOKENSFREE_REAL_EVIDENCE_SNAPSHOT": str(write_snapshot(tmp_path, "zhipu")),
    }
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    assert "only implemented for Windows" in str(caught.value)
