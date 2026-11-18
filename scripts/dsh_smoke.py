"""Run the real packaged DSH headless client against the new local backend.

The upstream is a synthetic, stateful SSE fixture: first turn asks for one read
tool call, second turn (after the tool result) returns a fixed final answer.
No real provider, no credentials, no user DSH home.

Run:  python scripts/dsh_smoke.py [path-to-electron-exe]
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tokensfree.config import load_config  # noqa: E402
from tokensfree.edge.asgi import make_asgi_app  # noqa: E402
from tokensfree.edge.backend import MemoryBackend  # noqa: E402
from tokensfree.edge.host import AsgiHost  # noqa: E402
from tokensfree.runtime import build_loopback_streaming_runtime  # noqa: E402

TOKEN = "smoke-" + "a" * 26
FINAL_TEXT = "DSH_SMOKE_OK"
CALL_ID = "call_dsh_smoke_1"
PACKAGED_DSH = pathlib.Path(r"D:\deepseek_harness\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js")
EXTRACTED_DSH = pathlib.Path(r"D:\IFan\dsh-extracted\node_modules\@deepseek-ai\dsh\lib\bin.js")
PACKAGED_ELECTRON = pathlib.Path(r"D:\deepseek_harness\DeepSeek Harness.exe")


def resolve_runtime(argv):
    """Pick the DSH entry point and the node binary that can read it.

    Order: explicit CLI args, then TOKENSFREE_DSH_BIN / TOKENSFREE_NODE_BIN,
    then a machine-local extracted copy, then the packaged app.asar copy.
    A plain node cannot read inside app.asar, so that copy needs Electron's node.
    """
    if len(argv) > 2:
        return pathlib.Path(argv[2]), pathlib.Path(argv[1])
    env_bin = os.environ.get("TOKENSFREE_DSH_BIN")
    if env_bin:
        bin_path = pathlib.Path(env_bin)
        env_node = os.environ.get("TOKENSFREE_NODE_BIN")
        if env_node:
            return bin_path, pathlib.Path(env_node)
        if "app.asar" in env_bin:
            return bin_path, PACKAGED_ELECTRON
        return bin_path, pathlib.Path("node")
    if EXTRACTED_DSH.exists():
        return EXTRACTED_DSH, pathlib.Path("node")
    return PACKAGED_DSH, PACKAGED_ELECTRON


def sse(*payloads):
    return "".join("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n" for payload in payloads) + "data: [DONE]\n\n"


class Fixture:
    def __init__(self, fixture_path):
        self.fixture_path = str(fixture_path)
        self.requests = []
        self.lock = threading.Lock()

    def respond(self, payload):
        messages = payload.get("messages") or []
        roles = [message.get("role") for message in messages if isinstance(message, dict)]
        tools = payload.get("tools") or []
        with self.lock:
            self.requests.append({"roles": roles, "tools": len(tools), "stream": payload.get("stream") is True})
            first_turn = "tool" not in roles
        if first_turn:
            return sse(
                {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": CALL_ID, "type": "function",
                                                                    "function": {"name": "read"}}]}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {
                    "arguments": json.dumps({"file_path": self.fixture_path, "offset": 1, "limit": 5})}}]},
                              "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            )
        return sse(
            {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": FINAL_TEXT}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        )


class UpstreamHandler(BaseHTTPRequestHandler):
    fixture = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        body = type(self).fixture.respond(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class BackendRunner:
    def __init__(self, app):
        self.host = AsgiHost(app, port=0)
        self.thread = threading.Thread(target=lambda: __import__("asyncio").run(self.host.run_forever()), daemon=True)

    def start(self):
        self.thread.start()
        deadline = time.time() + 10
        while self.host.port == 0 and time.time() < deadline:
            time.sleep(0.01)
        if not self.host.port:
            raise SystemExit("local backend did not start")
        return self.host.port

    def stop(self):
        self.host.stop()
        self.thread.join(10)


def write_patch(path, backend_port):
    path.write_text(
        f"""- id: agent-default-model
  config:
    provider: tokensfree-local
    model: fixture-model
- id: llm-pi-ai
  config:
    providers:
      tokensfree-local:
        apiKeyEnv: TOKENSFREE_LOCAL_TOKEN
        api: openai-completions
        baseURL: http://127.0.0.1:{backend_port}/v1
        compat:
          supportsDeveloperRole: false
          maxTokensField: max_tokens
        models:
          - id: fixture-model
- id: session-telemetry-otel
  disabled: true
- id: session-title-llm
  disabled: true
""",
        encoding="utf-8",
    )


def enable_parse_debug():
    """Print the rejected request's SHAPE (keys and types only, never content)."""
    import tokensfree.edge.app_core as app_core

    original = app_core.parse_chat_payload

    def shape(payload):
        if not isinstance(payload, dict):
            return {"payload_type": type(payload).__name__}
        messages = payload.get("messages")
        return {
            "keys": sorted(payload),
            "model_type": type(payload.get("model")).__name__,
            "stream": payload.get("stream"),
            "max_tokens": payload.get("max_tokens"),
            "max_completion_tokens": payload.get("max_completion_tokens"),
            "message_count": len(messages) if isinstance(messages, list) else None,
            "message_keys": sorted({key for message in messages if isinstance(message, dict) for key in message}) if isinstance(messages, list) else None,
            "roles": [message.get("role") for message in messages if isinstance(message, dict)] if isinstance(messages, list) else None,
            "content_types": sorted({type(message.get("content")).__name__ for message in messages if isinstance(message, dict)}) if isinstance(messages, list) else None,
            "tools": len(payload.get("tools") or []),
            "tool_choice": payload.get("tool_choice"),
        }

    def wrapped(payload, **kwargs):
        try:
            return original(payload, **kwargs)
        except app_core.RequestRejected:
            sys.stderr.write("PARSE_REJECT " + json.dumps(shape(payload), ensure_ascii=False) + "\n")
            sys.stderr.flush()
            raise

    # The streaming path imports the parser directly, so patch every module that holds it.
    import tokensfree.edge.protocol as protocol
    import tokensfree.edge.stream_core as stream_core

    protocol.parse_chat_payload = wrapped
    stream_core.parse_chat_payload = wrapped
    app_core.parse_chat_payload = wrapped


def main():
    if os.environ.get("TOKENSFREE_SMOKE_DEBUG") == "1":
        enable_parse_debug()
    dsh_bin, node_bin = resolve_runtime(sys.argv)
    if str(node_bin) != "node" and not pathlib.Path(node_bin).exists():
        raise SystemExit("node/electron binary not found: " + str(node_bin))
    runs_inside_asar = "app.asar" in str(dsh_bin)

    with tempfile.TemporaryDirectory(prefix="tokensfree-dsh-smoke-") as temp:
        temp = pathlib.Path(temp)
        home = temp / "home"
        home.mkdir()
        workspace = temp / "workspace"
        workspace.mkdir()
        fixture_file = workspace / "fixture.txt"
        fixture_file.write_text("line-one\nline-two\nline-three\n", encoding="utf-8")

        fixture = Fixture(fixture_file)
        UpstreamHandler.fixture = fixture
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()

        config = load_config({"TOKENSFREE_LOCAL_TOKEN": TOKEN})
        runtime = build_loopback_streaming_runtime(upstream.server_port, config=config, tools=True)
        backend = MemoryBackend(config=config, services=runtime.services, port=8765, token=TOKEN)
        runner = BackendRunner(make_asgi_app(backend))
        try:
            backend_port = runner.start()
            backend.port = backend_port
            patch = temp / "patch.yml"
            write_patch(patch, backend_port)

            # Keep the real Windows environment (SystemRoot, TEMP, APPDATA...); Node
            # cannot initialize its CSPRNG without them. Only the overrides are ours.
            env = dict(os.environ)
            env.setdefault("SYSTEMROOT", r"C:\Windows")
            env.setdefault("WINDIR", r"C:\Windows")
            env.setdefault("COMSPEC", r"C:\Windows\System32\cmd.exe")
            # Missing drive/app-data variables make some Windows APIs create literal
            # "%SystemDrive%/..." directories inside the current working directory.
            env.setdefault("SystemDrive", "C:")
            env.setdefault("HOMEDRIVE", "C:")
            env.setdefault("HOMEPATH", r"\Users\smoke")
            env.setdefault("ProgramData", r"C:\ProgramData")
            env.setdefault("ProgramFiles", r"C:\Program Files")
            env.setdefault("ProgramFiles(x86)", r"C:\Program Files (x86)")
            env.setdefault("APPDATA", str(home / "AppData" / "Roaming"))
            env.setdefault("LOCALAPPDATA", str(home / "AppData" / "Local"))
            env.setdefault("PUBLIC", r"C:\Users\Public")
            env.setdefault("TEMP", os.environ.get("TEMP", r"C:\Windows\Temp"))
            env.setdefault("USERNAME", os.environ.get("USERNAME", "smoke"))
            env.update(
                {
                    "HOME": str(home),
                    "USERPROFILE": str(home),
                    "DSH_HOME": str(home),
                    "TOKENSFREE_LOCAL_TOKEN": TOKEN,
                    "PYTHONIOENCODING": "utf-8",
                }
            )
            if runs_inside_asar:
                env["ELECTRON_RUN_AS_NODE"] = "1"
            prompt = (
                "Use the read tool to read fixture.txt in the current workspace. "
                "Then reply with the final confirmation text only."
            )
            started = time.time()
            try:
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
            except subprocess.TimeoutExpired:
                print(json.dumps({"verified": False, "reason": "dsh_timeout", "requests": len(fixture.requests)}))
                return 2

            events = []
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            final_texts = [event.get("text") for event in events if event.get("type") == "final"]
            event_types = [event.get("type") for event in events]
            first_turn, second_turn = (fixture.requests + [None, None])[:2]
            checks = {
                "dsh_exit_zero": result.returncode == 0,
                "first_turn_streamed": bool(first_turn and first_turn["stream"]),
                "first_turn_had_tools": bool(first_turn and first_turn["tools"] > 0),
                "second_turn_replayed_tool": bool(second_turn and "tool" in second_turn["roles"]),
                "one_tool_pair": event_types.count("tool_call") == 1 and event_types.count("tool_result") == 1,
                "final_text_ok": FINAL_TEXT in [text for text in final_texts if text],
            }
            summary = {
                "verified": all(checks.values()),
                "checks": checks,
                "request_count": len(fixture.requests),
                "event_types": event_types,
                "elapsed_s": round(time.time() - started, 1),
                "note": "synthetic upstream; no real provider, credentials or user DSH home",
            }
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            if not summary["verified"]:
                print("stderr tail:", (result.stderr or "").strip()[-3000:])
            return 0 if summary["verified"] else 3
        finally:
            runner.stop()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join()


if __name__ == "__main__":
    raise SystemExit(main())
