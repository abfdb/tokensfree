"""Generic OpenAI-compatible upstream adapter.

Default disabled: instantiate it explicitly with a base URL and a key. It refuses
plaintext remote hosts and refuses remote hosts unless allow_remote=True, so a
misconfiguration cannot quietly send traffic (and credentials) to the internet.

The key lives only in this object's memory for the duration of a request; it is
never logged and never included in error strings.
"""
import asyncio
import http.client
import json
import time
from urllib.parse import urlsplit

from tokensfree.adapters.base import Admission, ChatRequest, ChatResult, NormalizedError, classify_status
from tokensfree.adapters.stream_parse import parse_data_line

MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_LINE_BYTES = 65536
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class OpenAICompatAdapter:
    capabilities = frozenset({"text", "stream", "tools"})

    def __init__(self, base_url, api_key, *, timeout=15.0, max_stream_s=120.0, capabilities=None, allow_remote=False):
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("api_key must be a non-empty string")
        if not isinstance(base_url, str):
            raise ValueError("base_url must be a string")
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError("unsupported upstream scheme")
        if not parts.hostname:
            raise ValueError("upstream host missing")
        if parts.query or parts.fragment:
            raise ValueError("upstream url must not carry query or fragment")
        loopback = parts.hostname in LOOPBACK_HOSTS
        if parts.scheme == "http" and not loopback:
            raise ValueError("plaintext remote upstream refused")
        if not loopback and not allow_remote:
            raise ValueError("remote upstream requires allow_remote=True")
        if type(timeout) not in (int, float) or isinstance(timeout, bool) or not 0 < timeout <= 60:
            raise ValueError("invalid timeout")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._path = (parts.path.rstrip("/") or "") + "/chat/completions"
        self._key = api_key
        self._timeout = float(timeout)
        if type(max_stream_s) not in (int, float) or isinstance(max_stream_s, bool) or not 0 < max_stream_s <= 300:
            raise ValueError("invalid max_stream_s")
        self._max_stream_s = float(max_stream_s)
        self.capabilities = frozenset(capabilities) if capabilities is not None else type(self).capabilities

    @property
    def name(self):
        return "openai-compat"

    def _headers(self, *, stream):
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "Authorization": "Bearer " + self._key,
            "Connection": "close",
        }

    def _body(self, request, *, stream):
        if not isinstance(request, ChatRequest):
            raise TypeError("request must be a ChatRequest")
        payload = {
            "model": request.model,
            "messages": [dict(message) for message in request.messages],
            "max_tokens": request.max_tokens,
            "stream": bool(stream),
        }
        if request.tools:
            payload["tools"] = [dict(tool) for tool in request.tools]
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        return json.dumps(payload).encode("utf-8")

    def _connect(self):
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._host, self._port, timeout=self._timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def _retry_after(self, response):
        raw = response.getheader("Retry-After")
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    def _open(self, body, *, stream):
        connection = self._connect()
        connection.request("POST", self._path, body=body, headers=self._headers(stream=stream))
        response = connection.getresponse()
        if response.status != 200:
            error = classify_status(response.status, self._retry_after(response))
            error.provider = self.name
            connection.close()
            raise error
        return connection, response

    async def complete(self, request, admission):
        if not isinstance(admission, Admission):
            raise TypeError("admission must be issued by the router")
        body = self._body(request, stream=False)
        return await asyncio.to_thread(self._post, body)

    def _post(self, body):
        connection = None
        try:
            connection, response = self._open(body, stream=False)
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                raise NormalizedError("invalid_response", retryable=False, provider=self.name)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                raise NormalizedError("invalid_response", retryable=False, provider=self.name) from None
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices:
                raise NormalizedError("invalid_response", retryable=False, provider=self.name)
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise NormalizedError("invalid_response", retryable=False, provider=self.name)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            return ChatResult(content=content, finish_reason=choices[0].get("finish_reason") or "stop", usage=usage)
        except (OSError, http.client.HTTPException):
            raise NormalizedError("disconnected", retryable=True, provider=self.name) from None
        finally:
            if connection is not None:
                connection.close()

    async def stream(self, request, admission):
        if not isinstance(admission, Admission):
            raise TypeError("admission must be issued by the router")
        iterator = self._iter_lines(self._body(request, stream=True))
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

    def _iter_lines(self, body):
        connection = None
        try:
            connection, response = self._open(body, stream=True)
            deadline = time.monotonic() + self._max_stream_s
            while True:
                line = response.readline(MAX_LINE_BYTES + 1)
                if not line:
                    break
                if time.monotonic() > deadline:
                    raise NormalizedError("timeout", retryable=True, provider=self.name)
                if len(line) > MAX_LINE_BYTES:
                    raise NormalizedError("invalid_response", retryable=False, provider=self.name)
                yield line.decode("utf-8", errors="strict")
        except (OSError, http.client.HTTPException):
            raise NormalizedError("disconnected", retryable=True, provider=self.name) from None
        finally:
            if connection is not None:
                connection.close()
