"""The direct real-provider probe: fixture self-test and fail-closed env handling."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def probe_env(**overrides):
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("TOKENSFREE_REAL_") or key == "TOKENSFREE_ENABLE_REAL":
            env.pop(key, None)
    env.update(overrides)
    return env


def run_probe(*args, env=None, timeout=120):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "real_probe.py"), *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env or dict(os.environ),
    )


def test_help_mentions_the_flags():
    result = run_probe("--help")
    assert result.returncode == 0
    assert "--fixture" in result.stdout
    assert "--stream" in result.stdout
    assert "--tools" in result.stdout


def test_real_mode_without_environment_fails_closed():
    result = run_probe(env=probe_env())
    assert result.returncode == 2
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is False
    assert "TOKENSFREE_REAL_BASE_URL" in payload["missing"]


def test_fixture_mode_validates_all_three_paths():
    result = run_probe("--fixture", "--stream", "--tools", env=probe_env())
    assert result.returncode == 0, (result.stdout[-2000:] + result.stderr[-2000:])
    payload = json.loads(result.stdout[result.stdout.index("{"):])
    assert payload["verified"] is True
    assert payload["checks"] == {"nonstream_ok": True, "stream_ok": True, "tools_ok": True}
    assert payload["details"]["tool_calls"] == ["read"]
    assert "sk-fixture-only" not in (result.stdout + result.stderr)



def test_real_mode_retries_transient_429(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = {"count": 0}
    json_ok = json.dumps(
        {
            "model": "upstream-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 2},
        }
    ).encode()
    sse_ok = (
        'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            calls["count"] += 1
            if calls["count"] <= 2:
                body = json.dumps({"error": {"code": "1305", "message": "busy"}}).encode()
                self.send_response(429)
            else:
                body = sse_ok if payload.get("stream") else json_ok
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = probe_env(
            TOKENSFREE_REAL_PROVIDER="zhipu-test",
            TOKENSFREE_REAL_BASE_URL="http://127.0.0.1:%d/v1" % server.server_port,
            TOKENSFREE_REAL_MODEL="upstream-model",
            TOKENSFREE_REAL_API_KEY="sk-test-key",
            TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT=str(time.time() + 3600),
            TOKENSFREE_ENABLE_REAL="1",
        )
        result = run_probe("--retry-delays", "0,0", env=env)
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout[result.stdout.index("{"):])
        assert payload["verified"] is True
        assert payload["attempts"]["nonstream"] == 3
        assert "sk-test-key" not in (result.stdout + result.stderr)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(10)
