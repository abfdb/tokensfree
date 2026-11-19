"""Cheap direct probe of the configured real provider.

Exercises the product's own runtime + OpenAI-compatible adapter (not the gateway
or DSH), so it validates the exact code path a real deployment uses, with a
handful of tokens per call.

  --fixture   self-contained: local fake upstream, no network, no key
  (default)   uses TOKENSFREE_REAL_* from the environment

Never prints the API key or the full prompt.
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tokensfree.adapters.base import ChatRequest, NormalizedError  # noqa: E402
from tokensfree.config import load_config  # noqa: E402
from tokensfree.runtime import EVIDENCE_SOURCE_VARS, KEY_SOURCE_VARS, REAL_ENV_KEYS, build_real_runtime  # noqa: E402

PROMPT = "Reply with the single word OK and nothing else."
TOOL_PROMPT = "You must call the read tool with path probe.txt. Do not reply with plain text."
# Free tiers throttle (Zhipu code 1305); a few spaced retries turn that into success.
RETRY_DELAYS = (5, 8, 12, 15, 20, 25)
REAL_REQUIRED = tuple(
    key for key in REAL_ENV_KEYS if key not in EVIDENCE_SOURCE_VARS + ("TOKENSFREE_REAL_API_KEY",)
) + ("TOKENSFREE_ENABLE_REAL",)
FIXTURE_JSON = json.dumps(
    {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
)
FIXTURE_TOOL_CALLS = json.dumps(
    {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_probe_1", "type": "function", "function": {"name": "read", "arguments": "{\"path\":\"probe.txt\"}"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
)
FIXTURE_SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


class FixtureUpstream:
    def __init__(self):
        self.requests = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    payload = {}
                owner.requests += 1
                if payload.get("stream"):
                    body, content_type = FIXTURE_SSE.encode(), "text/event-stream; charset=utf-8"
                elif payload.get("tools"):
                    body, content_type = FIXTURE_TOOL_CALLS.encode(), "application/json"
                else:
                    body, content_type = FIXTURE_JSON.encode(), "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
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


def main():
    parser = argparse.ArgumentParser(description="Direct probe of the configured real provider")
    parser.add_argument("--fixture", action="store_true", help="local fake upstream instead of a real provider")
    parser.add_argument("--stream", action="store_true", help="also probe the streaming path")
    parser.add_argument("--tools", action="store_true", help="also probe a tool-call request")
    parser.add_argument("--max-tokens", type=int, default=512, help="completion budget per probe (reasoning models need room)")
    parser.add_argument("--retries", type=int, default=len(RETRY_DELAYS) + 1, help="attempts for transient 429/5xx")
    parser.add_argument("--retry-delays", default="", help="comma separated retry delays in seconds (testing)")
    args = parser.parse_args()

    env = dict(os.environ)
    fixture = None
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
        mode = "fixture"
    else:
        missing = [key for key in REAL_REQUIRED if not str(env.get(key, "")).strip()]
        if not any(str(env.get(key, "")).strip() for key in KEY_SOURCE_VARS):
            missing.append("TOKENSFREE_REAL_API_KEY")
        if not any(str(env.get(key, "")).strip() for key in EVIDENCE_SOURCE_VARS):
            missing.append("TOKENSFREE_REAL_EVIDENCE_EXPIRES_AT")
        if missing:
            print(json.dumps({"verified": False, "reason": "missing_env", "missing": missing}, indent=2))
            return 2
        mode = "real"

    if not 1 <= args.max_tokens <= 8192:
        print(json.dumps({"verified": False, "reason": "invalid_max_tokens"}, indent=2))
        return 2
    if not 1 <= args.retries <= 10:
        print(json.dumps({"verified": False, "reason": "invalid_retries"}, indent=2))
        return 2
    delays = RETRY_DELAYS
    if args.retry_delays:
        try:
            delays = tuple(float(item) for item in args.retry_delays.split(",") if item.strip())
        except ValueError:
            print(json.dumps({"verified": False, "reason": "invalid_retry_delays"}, indent=2))
            return 2
        if not delays or any(delay < 0 or delay > 120 for delay in delays):
            print(json.dumps({"verified": False, "reason": "invalid_retry_delays"}, indent=2))
            return 2

    attempts = {}

    def call_with_retry(label, action):
        attempts[label] = 0

        def attempt():
            attempts[label] += 1
            return action()

        for index in range(args.retries):
            try:
                return attempt()
            except NormalizedError as error:
                if not error.retryable or index == args.retries - 1:
                    raise ProbeFailure(label, error, attempts[label]) from None
                time.sleep(delays[min(index, len(delays) - 1)])
        raise AssertionError("unreachable")

    def stream_with_retry(label, make_iterator):
        attempts[label] = 0
        for index in range(args.retries):
            events = []
            try:
                attempts[label] += 1
                async def consume():
                    async for event in make_iterator():
                        events.append(event)

                asyncio.run(consume())
                return events
            except NormalizedError as error:
                if events:
                    raise ProbeFailure(label, error, attempts[label]) from None
                if not error.retryable or index == args.retries - 1:
                    raise ProbeFailure(label, error, attempts[label]) from None
                time.sleep(delays[min(index, len(delays) - 1)])
        raise AssertionError("unreachable")

    class ProbeFailure(Exception):
        def __init__(self, label, error, attempts_made):
            self.label = label
            self.code = getattr(error, "code", "unknown")
            self.retryable = bool(getattr(error, "retryable", False))
            self.attempts = attempts_made
            self.retry_after_s = getattr(error, "retry_after_s", None)
            super().__init__(self.code)

    checks = {}
    failures = {}
    started = time.time()
    try:
        config = load_config({key: value for key, value in env.items() if key.startswith("TOKENSFREE_")})
        runtime = build_real_runtime(config=config, env=env)
        spec = runtime.services.specs[0]
        adapter = runtime.services.adapters[spec.name]
        admission = runtime.services.admit(spec)
        base_request = {"model": spec.model, "messages": ({"role": "user", "content": PROMPT},)}

        step = time.time()
        try:
            result = call_with_retry(
                "nonstream",
                lambda: asyncio.run(adapter.complete(ChatRequest(**base_request, max_tokens=args.max_tokens), admission)),
            )
            checks["nonstream_ok"] = bool(result.content.strip()) or bool(getattr(result, "tool_calls", ()))
            latencies = {
                "nonstream_s": round(time.time() - step, 2),
                "finish_reason": result.finish_reason,
                "usage": bool(result.usage),
                "model_reported": getattr(result, "model", None),
            }
        except ProbeFailure as failure:
            failures["nonstream"] = {"code": failure.code, "retryable": failure.retryable, "attempts": failure.attempts}
            latencies = {"nonstream_s": round(time.time() - step, 2)}
            checks["nonstream_ok"] = False

        if args.stream:
            step = time.time()
            try:
                events = stream_with_retry(
                    "stream",
                    lambda: adapter.stream(ChatRequest(**base_request, max_tokens=args.max_tokens), admission),
                )
                kinds = [event.kind for event in events]
                text = "".join((event.delta or {}).get("content", "") for event in events if event.kind == "delta")
                checks["stream_ok"] = bool(text.strip()) and ("finish" in kinds or "done" in kinds)
                latencies["stream_s"] = round(time.time() - step, 2)
                latencies["stream_kinds"] = kinds
                latencies["stream_content_chars"] = len(text)
            except ProbeFailure as failure:
                failures["stream"] = {"code": failure.code, "retryable": failure.retryable, "attempts": failure.attempts}
                checks["stream_ok"] = False

        if args.tools:
            step = time.time()
            tools = (
                {
                    "type": "function",
                    "function": {
                        "name": "read",
                        "description": "Read a text file inside the workspace",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                },
            )
            try:
                tool_result = call_with_retry(
                    "tools",
                    lambda: asyncio.run(
                        adapter.complete(
                            ChatRequest(model=spec.model, messages=({"role": "user", "content": TOOL_PROMPT},), max_tokens=args.max_tokens, tools=tools, tool_choice="auto"),
                            admission,
                        )
                    ),
                )
                calls = getattr(tool_result, "tool_calls", ())
                checks["tools_ok"] = bool(calls)
                latencies["tools_s"] = round(time.time() - step, 2)
                latencies["tool_calls"] = [call.get("function", {}).get("name") for call in calls]
            except ProbeFailure as failure:
                failures["tools"] = {"code": failure.code, "retryable": failure.retryable, "attempts": failure.attempts}
                checks["tools_ok"] = False
    finally:
        if fixture is not None:
            fixture.stop()

    summary = {
        "verified": all(checks.values()) and bool(checks),
        "mode": mode,
        "checks": checks,
        "provider": env.get("TOKENSFREE_REAL_PROVIDER"),
        "model": env.get("TOKENSFREE_REAL_MODEL"),
        "details": latencies,
        "attempts": attempts,
        "failures": failures,
        "max_tokens": args.max_tokens,
        "elapsed_s": round(time.time() - started, 2),
        "note": "direct adapter probe; no gateway, no DSH, key never printed",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["verified"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
