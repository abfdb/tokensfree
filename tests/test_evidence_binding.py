"""TOKENSFREE_REAL_EVIDENCE_SNAPSHOT: expiry read from a recorded snapshot, bound to model."""
import asyncio
import json
import pathlib

import pytest

from test_multi_key import CONFIG_ON, MODEL, PROVIDER, env_for
from tokensfree.edge.app_core import handle_chat
from tokensfree.runtime import build_real_runtime
from upstream_support import JsonUpstream

OK_BODY = '{"choices":[{"index":0,"message":{"role":"assistant","content":"snapshot ok"},"finish_reason":"stop"}],"usage":{"total_tokens":2}}'
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def snapshot_dict(**overrides):
    data = {
        "version": 1,
        "provider": PROVIDER,
        "model": MODEL,
        "source": "https://example.invalid/pricing.md",
        "sha256": "a" * 64,
        "evidence_expires_at": 2000,
    }
    data.update(overrides)
    return data


def write_snapshot(tmp_path, **overrides):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(snapshot_dict(**overrides)), encoding="utf-8")
    return path


def env_with_snapshot(port, path):
    env = env_for(port, None, key="snapshot-key")
    env.pop("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT", None)
    env["TOKENSFREE_REAL_EVIDENCE_SNAPSHOT"] = str(path)
    return env


def upstream():
    server = JsonUpstream(OK_BODY)
    server.start()
    return server


def test_snapshot_source_replaces_the_inline_timestamp(tmp_path):
    server = upstream()
    try:
        runtime = build_real_runtime(
            config=CONFIG_ON,
            env=env_with_snapshot(server.port, write_snapshot(tmp_path)),
            clock=lambda: 1000.0,
        )
        assert runtime.services.specs[0].evidence_expires_at == 2000.0
        outcome = asyncio.run(
            handle_chat(
                {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
                services=runtime.services,
                request_id="req-snapshot",
            )
        )
        assert outcome.status == 200
        assert outcome.body["choices"][0]["message"]["content"] == "snapshot ok"
    finally:
        server.stop()


def test_snapshot_bound_to_model(tmp_path):
    path = write_snapshot(tmp_path, model="glm-9-paid")
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env_with_snapshot(1, path), clock=lambda: 1000.0)
    assert "is for model" in str(caught.value)


def test_snapshot_bound_to_provider(tmp_path):
    path = write_snapshot(tmp_path, provider="other-provider")
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env_with_snapshot(1, path), clock=lambda: 1000.0)
    assert "is for provider" in str(caught.value)


def test_both_evidence_sources_are_rejected(tmp_path):
    path = write_snapshot(tmp_path)
    env = env_with_snapshot(1, path)
    env["TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT"] = "2000"
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    assert "only one of" in str(caught.value)


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"evidence_expires_at": 500}, "past"),
        ({"evidence_expires_at": "soon"}, "numeric"),
        ({"model": ""}, "no model"),
        ({"provider": ""}, "ok"),
    ],
)
def test_invalid_snapshots_fail_closed(tmp_path, payload, message):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(snapshot_dict(**payload)), encoding="utf-8")
    env = env_with_snapshot(1, path)
    if message == "ok":
        # empty provider is allowed (binding to model is what matters)
        runtime = build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
        assert runtime.services.specs[0].evidence_expires_at == 2000.0
        return
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    assert message in str(caught.value)


def test_missing_snapshot_file_fails_closed(tmp_path):
    env = env_with_snapshot(1, tmp_path / "absent.json")
    with pytest.raises(ValueError) as caught:
        build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    assert "cannot read evidence snapshot" in str(caught.value)


def test_recorded_zhipu_snapshot_is_usable():
    evidence_dir = REPO_ROOT / ".superpowers" / "evidence"
    files = sorted(evidence_dir.glob("zhipu-*.json")) if evidence_dir.exists() else []
    if not files:
        pytest.skip("no recorded evidence snapshot in this checkout")
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    env = env_for(1, None, key="snapshot-key")
    env.pop("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT", None)
    env["TOKENSFREE_REAL_PROVIDER"] = data["provider"]
    env["TOKENSFREE_REAL_MODEL"] = data["model"]
    env["TOKENSFREE_REAL_EVIDENCE_SNAPSHOT"] = str(files[-1])
    runtime = build_real_runtime(config=CONFIG_ON, env=env, clock=lambda: 1000.0)
    spec = runtime.services.specs[0]
    assert spec.evidence_expires_at == float(data["evidence_expires_at"])
    assert spec.model == data["model"]
