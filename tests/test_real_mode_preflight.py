"""Offline preflight of the exact real-mode command path: key file + evidence snapshot.

Starts python -m tokensfree --real against a local fake Zhipu-compatible upstream and
exercises models, non-streaming, streaming and the account-aware failure path.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "r" * 32
MODEL = "glm-4.7-flash"
PROVIDER = "zhipu"
KEY = "preflight-key-0123456789"

SSE_FRAMES = (
    'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
    'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)
JSON_OK = json.dumps(
    {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }
)
AUTH_ERROR = json.dumps({"error": {"code": "401", "message": "token expired"}})


class FakeZhipu:
    def __init__(self, *, auth_failure=False):
        self.auth_failure = auth_failure
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                payload = json.loads(raw or b"{}")
                owner.requests.append(payload)
                if owner.auth_failure:
                    body = AUTH_ERROR.encode()
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if payload.get("stream"):
                    data = SSE_FRAMES.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    data = JSON_OK.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = None

    @property
    def port(self):
        return self.server.server_port

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(10)


def write_env_files(temp: Path, upstream_port, *, model=MODEL, provider=PROVIDER, expired=False):
    key_file = temp / "zhipu.key"
    key_file.write_text(KEY + "\n", encoding="utf-8")
    snapshot = temp / "evidence.json"
    snapshot.write_text(
        json.dumps(
            {
                "version": 1,
                "provider": provider,
                "model": model,
                "source": "https://example.invalid/pricing.md",
                "sha256": "b" * 64,
                "evidence_expires_at": 500 if expired else time.time() + 86400,
            }
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "TOKENSFREE_LOCAL_TOKEN": TOKEN,
            "TOKENSFREE_ENABLE_REAL": "1",
            "TOKENSFREE_REAL_PROVIDER": provider,
            "TOKENSFREE_REAL_BASE_URL": "http://127.0.0.1:%d/v1" % upstream_port,
            "TOKENSFREE_REAL_MODEL": model,
            "TOKENSFREE_REAL_API_KEY_FILE": str(key_file),
            "TOKENSFREE_REAL_EVIDENCE_SNAPSHOT": str(snapshot),
        }
    )
    return env, key_file, snapshot


def start_gateway(env, db_path):
    process = subprocess.Popen(
        [sys.executable, "-m", "tokensfree", "--real", "--port", "0", "--db", str(db_path)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    lines = []

    def reader():
        for line in process.stdout:
            lines.append(line.strip())
            if "http://127.0.0.1:" in line:
                return

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.time() + 25
    while time.time() < deadline and not any("http://127.0.0.1:" in line for line in lines):
        if process.poll() is not None:
            break
        time.sleep(0.05)
    assert any("http://127.0.0.1:" in line for line in lines), (lines, process.stderr.read() if process.poll() is not None else "")
    port = int(re.search(r"http://127\.0\.0\.1:(\d+)", lines[-1]).group(1))
    return process, port


def stop_gateway(process):
    process.terminate()
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def request(port, path, payload=None, *, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (port, path),
        data=data,
        method=method,
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")


def test_real_mode_serves_models_chat_and_stream(tmp_path):
    upstream = FakeZhipu().start()
    process = None
    try:
        env, _key_file, _snapshot = write_env_files(tmp_path, upstream.port)
        process, port = start_gateway(env, tmp_path / "state.db")
        status, body = request(port, "/v1/models", method="GET")
        assert status == 200
        models = json.loads(body)
        assert any(model["id"] == MODEL for model in models["data"])

        status, body = request(
            port, "/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
        )
        assert status == 200
        assert json.loads(body)["choices"][0]["message"]["content"] == "OK"

        status, body = request(
            port,
            "/v1/chat/completions",
            {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )
        assert status == 200
        assert body.strip().endswith("data: [DONE]")
        assert '"content": "OK"' in body

        status, body = request(port, "/api/status", method="GET")
        payload = json.loads(body)
        assert payload["simulation"] is False
        assert payload["providers"][0]["state"] == "eligible"
        assert payload["providers"][0]["model"] == MODEL
        assert payload["accounts"][0]["status"] == "active"
        assert payload["accounts"][0]["requests_charged"] == 2
        assert upstream.requests and all(item.get("model") == MODEL for item in upstream.requests)
    finally:
        if process is not None:
            stop_gateway(process)
        upstream.stop()


def test_real_mode_auth_failure_marks_the_account_invalid(tmp_path):
    upstream = FakeZhipu(auth_failure=True).start()
    process = None
    try:
        env, _key_file, _snapshot = write_env_files(tmp_path, upstream.port)
        process, port = start_gateway(env, tmp_path / "state.db")
        status, body = request(
            port, "/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": "hi"}]}
        )
        assert status == 502
        error = json.loads(body)["error"]["code"]
        assert error == "upstream_auth_failed"

        status, body = request(port, "/api/status", method="GET")
        payload = json.loads(body)
        assert payload["accounts"][0]["status"] == "invalid"
        assert payload["providers"][0]["state"] == "blocked"
        assert payload["providers"][0]["policy_reason"] == "account_invalid"
    finally:
        if process is not None:
            stop_gateway(process)
        upstream.stop()


def test_real_mode_refuses_expired_evidence(tmp_path):
    upstream = FakeZhipu().start()
    try:
        env, _key_file, _snapshot = write_env_files(tmp_path, upstream.port, expired=True)
        result = subprocess.run(
            [sys.executable, "-m", "tokensfree", "--real", "--port", "0", "--db", str(tmp_path / "state.db")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=env,
        )
        output = result.stdout + result.stderr
        assert result.returncode != 0
        assert "past" in output
        assert "Traceback" not in output
        assert upstream.requests == []
    finally:
        upstream.stop()
