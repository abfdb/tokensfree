"""Upstream HTTP transport over stdlib http.client.

Scope for this phase: literal 127.0.0.1 only, no proxy, no redirects, hard
deadline, bounded line length. Real origins need D2 and a separate allowlist.
"""
import asyncio
import http.client
import json
import time

from tokensfree.adapters.base import Admission, ChatRequest, NormalizedError, classify_status
from tokensfree.adapters.stream_parse import parse_data_line

FIXTURE_TOKEN = "tokensfree-offline-fixture-only"
MAX_LINE_BYTES = 65536


class LoopbackSSETransport:
    def __init__(self, port, *, timeout=5.0, max_stream_s=60.0, path="/v1/chat/completions", token=FIXTURE_TOKEN):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("invalid loopback port")
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 < timeout <= 30:
            raise ValueError("invalid timeout")
        if not isinstance(path, str) or not path.startswith("/") or "://" in path:
            raise ValueError("invalid path")
        if not isinstance(token, str) or not token:
            raise ValueError("invalid token")
        if type(max_stream_s) not in (int, float) or isinstance(max_stream_s, bool) or not 0 < max_stream_s <= 300:
            raise ValueError("invalid max_stream_s")
        self.port = port
        self.timeout = float(timeout)
        self.path = path
        self.token = token
        self.max_stream_s = float(max_stream_s)

    def _retry_after(self, response):
        raw = response.getheader("Retry-After")
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def iter_lines(self, body):
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError("body must be bytes")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request(
                "POST",
                self.path,
                body=bytes(body),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.token,
                    "Accept": "text/event-stream",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                error = classify_status(response.status, self._retry_after(response))
                error.provider = "loopback"
                raise error
            deadline = time.monotonic() + self.max_stream_s
            while True:
                line = response.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if time.monotonic() > deadline:
                    raise NormalizedError("timeout", retryable=True, provider="loopback")
                if len(line) > MAX_LINE_BYTES:
                    raise NormalizedError("invalid_response", retryable=False, provider="loopback")
                yield line.decode("utf-8", errors="strict")
        except (OSError, http.client.HTTPException):
            raise NormalizedError("disconnected", retryable=True, provider="loopback") from None
        finally:
            connection.close()


class LoopbackStreamingAdapter:
    name = "loopback"
    capabilities = frozenset({"text", "stream"})

    def __init__(self, port, *, timeout=5.0, transport=None):
        self.transport = LoopbackSSETransport(port, timeout=timeout) if transport is None else transport

    async def stream(self, request, admission):
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be a ChatRequest")
        if not isinstance(admission, Admission):
            raise TypeError("admission must be issued by the router")
        payload = {
            "model": request.model or "fixture-model",
            "messages": [dict(message) for message in request.messages],
            "stream": True,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = [dict(tool) for tool in request.tools]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.response_format is not None:
            payload["response_format"] = dict(request.response_format)
        if request.stream_include_usage:
            payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload).encode("utf-8")
        iterator = self.transport.iter_lines(body)
        sentinel = object()
        while True:
            line = await asyncio.to_thread(next, iterator, sentinel)
            if line is sentinel:
                return
            event = parse_data_line(line)
            if event is None:
                continue
            yield event
            if event.kind == "done":
                return
