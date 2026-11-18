"""Acceptance smoke for a real provider routed through tokensfree.

Two modes:
  --fixture  self-contained: starts a local fake OpenAI-compatible SSE upstream and
             points the real-provider configuration at it. No network, no key.
  (default)  uses TOKENSFREE_REAL_* from the environment and talks to the real provider.

Both modes start `python -m tokensfree --real`, then drive the real packaged DSH
headless client through the gateway and check the final answer.
DSH paths: TOKENSFREE_DSH_BIN / TOKENSFREE_NODE_BIN, or a machine-local extracted copy.
"""
import argparse
import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOKEN = "s" * 32
DEFAULT_PORT = 18768
PACKAGED_DSH = pathlib.Path(r"D:\deepseek_harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js")
EXTRACTED_DSH = pathlib.Path(r"D:\IFan\dsh-extracted\node_modules\@deepseek-ai\dsh\lib\bin.js")
PACKAGED_ELECTRON = pathlib.Path(r"D:\deepseek_harness\DeepSeek Harness.exe")
REAL_KEYS = (
    "TOKENSFREE_REAL_PROVIDER",
    "TOKENSFREE_REAL_BASE_URL",
    "TOKENSFREE_REAL_MODEL",
)
EVIDENCE_SOURCE_VARS = (
    "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT",
    "TOKENSFREE_REAL_EVIDENCE_SNAPSHOT",
)
KEY_SOURCE_VARS = (
    "TOKENSFREE_REAL_API_KEY",
    "TOKENSFREE_REAL_API_KEYS",
    "TOKENSFREE_REAL_API_KEY_FILE",
    "TOKENSFREE_REAL_API_KEYS_FILE",
)
FIXTURE_SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def resolve_dsh():
    bin_path = os.environ.get("TOKENSFREE_DSH_BIN")
    node_path = os.environ.get("TOKENSFREE_NODE_BIN")
    if bin_path:
        return pathlib.Path(bin_path), pathlib.Path(node_path) if node_path else pathlib.Path("node")
    if EXTRACTED_DSH.exists():
        return EXTRACTED_DSH, pathlib.Path("node")
    return PACKAGED_DSH, PACKAGED_ELECTRON


class FixtureUpstream:
    def __init__(self):
        self.requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                owner.requests += 1
                body = FIXTURE_SSE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = None

    @property
    def port(self):
        return self._server.server_port

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(10)


def wait_port(port, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


def call(path, *, method="GET", body=None, port=DEFAULT_PORT, token=TOKEN):
    headers = {"Host": "127.0.0.1:" + str(port), "Authorization": "Bearer " + token}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        response = connection.getresponse()
        raw = response.read()
        return response.status, raw
    finally:
        connection.close()


def write_patch(path, port, model):
    path.write_text(
        f"""- id: agent-default-model
  config:
    provider: tokensfree-local
    model: {model}
- id: llm-pi-ai
  config:
    providers:
      tokensfree-local:
        apiKeyEnv: TOKENSFREE_LOCAL_TOKEN
        api: openai-completions
        baseURL: http://127.0.0.1:{port}/v1
        compat:
          supportsDeveloperRole: false
          maxTokensField: max_tokens
        models:
          - id: {model}
- id: session-telemetry-otel
  disabled: true
- id: session-title-llm
  disabled: true
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Acceptance smoke for a real provider through tokensfree")
    parser.add_argument("--fixture", action="store_true",
                        help="self-contained mode: local fake upstream instead of a real provider")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    checks = {}
    fixture = None
    env = dict(os.environ)
    env["TOKENSFREE_LOCAL_TOKEN"] = TOKEN

    if args.fixture:
        fixture = FixtureUpstream()
        fixture.start()
        env.update(
            {
                "TOKENSFREE_REAL_PROVIDER": "fixture-real",
                "TOKENSFREE_REAL_BASE_URL": "http://127.0.0.1:" + str(fixture.port) + "/v1",
                "TOKENSFREE_REAL_API_KEY": "sk-fixture-only",
                "TOKENSFREE_REAL_MODEL": "fixture-real-model",
                "TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT": str(time.time() + 3600),
                "TOKENSFREE_ENABLE_REAL": "1",
            }
        )
        model = env["TOKENSFREE_REAL_MODEL"]
        mode = "fixture"
    else:
        missing = [key for key in REAL_KEYS if not str(env.get(key, "")).strip()]
        if not any(str(env.get(key, "")).strip() for key in KEY_SOURCE_VARS):
            missing.append("TOKENSFREE_REAL_API_KEY")
        if not any(str(env.get(key, "")).strip() for key in EVIDENCE_SOURCE_VARS):
            missing.append("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT")
        if env.get("TOKENSFREE_ENABLE_REAL") != "1":
            missing.append("TOKENSFREE_ENABLE_REAL=1")
        if missing:
            print(json.dumps({"verified": False, "reason": "missing_env", "missing": missing}, ensure_ascii=False, indent=2))
            return 2
        model = env["TOKENSFREE_REAL_MODEL"]
        mode = "real"

    dsh_bin, node_bin = resolve_dsh()
    if not pathlib.Path(str(dsh_bin)).exists():
        print(json.dumps({"verified": False, "reason": "dsh_not_found", "path": str(dsh_bin)}))
        return 2

    with tempfile.TemporaryDirectory(prefix="tokensfree-real-smoke-") as temp:
        temp = pathlib.Path(temp)
        home, workspace = temp / "home", temp / "workspace"
        home.mkdir()
        workspace.mkdir()
        db_path = temp / "state.db"
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["DSH_HOME"] = str(home)
        env.setdefault("SYSTEMROOT", r"C:\Windows")
        env.setdefault("WINDIR", r"C:\Windows")
        env.setdefault("COMSPEC", r"C:\Windows\System32\cmd.exe")
        env.setdefault("SystemDrive", "C:")
        env.setdefault("TEMP", os.environ.get("TEMP", r"C:\Windows\Temp"))
        env.setdefault("TMP", env["TEMP"])

        server = subprocess.Popen(
            [sys.executable, "-m", "tokensfree", "--port", str(args.port), "--real", "--db", str(db_path)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            checks["gateway_started"] = wait_port(args.port)
            if not checks["gateway_started"]:
                checks["verified"] = False
                print(json.dumps({"verified": False, "checks": checks, "reason": "gateway_did_not_start"}, indent=2))
                return 3
            status, raw = call("/api/status")
            status_payload = json.loads(raw) if status == 200 else {}
            checks["real_mode_reported"] = (
                status == 200
                and status_payload.get("simulation") is False
                and any(provider.get("state") == "eligible" for provider in status_payload.get("providers", []))
            )

            patch = temp / "patch.yml"
            write_patch(patch, args.port, model)
            prompt = "Reply with the single word OK and nothing else."
            started = time.time()
            result = subprocess.run(
                [str(node_bin), str(dsh_bin), "--profile", "headless", "--patch", str(patch), "--json", prompt],
                cwd=str(workspace),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            events = []
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
            final_texts = [event.get("text") for event in events if event.get("type") == "final"]
            checks["dsh_exit_zero"] = result.returncode == 0
            checks["final_text_nonempty"] = bool([text for text in final_texts if text])
            if fixture is not None:
                checks["upstream_was_called"] = fixture.requests > 0
        finally:
            server.terminate()
            try:
                server.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.communicate()
            if fixture is not None:
                fixture.stop()

    summary = {
        "verified": all(checks.values()) and bool(checks),
        "mode": mode,
        "checks": checks,
        "provider": env.get("TOKENSFREE_REAL_PROVIDER"),
        "model": model,
        "elapsed_s": round(time.time() - started, 1),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verified"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
