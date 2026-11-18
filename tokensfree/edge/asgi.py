"""ASGI 3.0 adapter over MemoryBackend. No third-party dependencies.

Any ASGI server (uvicorn, hypercorn) or a larger ASGI app can mount this callable.
It only translates the ASGI protocol; every decision stays in the backend contract.

One structured access-log line is emitted per request (method, path, status,
latency, request id). Bodies and prompts never enter it.
"""
import json
import time

from tokensfree import obs
from tokensfree.adapters.base import NormalizedError
from tokensfree.edge.backend import EdgeRequest
from tokensfree.edge.protocol import build_error, map_upstream_error
from tokensfree.errors import ErrorCode
from tokensfree.router.execute import NoProviderAvailable

MAX_LOGGED_PATH = 200


class BodyTooLarge(Exception):
    pass


async def _read_request(scope, receive, limit):
    headers = {}
    for raw_name, raw_value in scope.get("headers", ()):
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        headers[name] = value if name not in headers else headers[name] + ", " + value
    chunks = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            raise RuntimeError("unexpected ASGI message")
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise BodyTooLarge()
        chunks.append(chunk)
        if not message.get("more_body", False):
            break
    return EdgeRequest(
        method=scope.get("method", "GET"),
        path=scope.get("path", "/"),
        headers=headers,
        body=b"".join(chunks),
    )


def _send_headers(headers):
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()]


async def _send_json_error(send, code):
    status, payload = build_error(code, "req-stream")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
    await send({"type": "http.response.start", "status": status, "headers": _send_headers(headers)})
    await send({"type": "http.response.body", "body": body, "more_body": False})


def make_asgi_app(backend):
    if not hasattr(backend, "handle"):
        raise TypeError("backend must implement handle()")

    async def app(scope, receive, send):
        if scope.get("type") != "http":
            raise RuntimeError("only http scope is supported")
        started = time.monotonic()
        info = {"status": None, "request_id": None}

        async def send_tracked(message):
            if message.get("type") == "http.response.start":
                info["status"] = message.get("status")
                for name, value in message.get("headers", []):
                    if name.lower() == b"x-request-id":
                        info["request_id"] = value.decode("latin-1", errors="replace")
            await send(message)

        try:
            try:
                request = await _read_request(scope, receive, backend.config.max_body_bytes)
            except BodyTooLarge:
                status, payload = build_error(ErrorCode.payload_too_large, "req-oversized")
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                headers = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
                await send_tracked(
                    {"type": "http.response.start", "status": status, "headers": _send_headers(headers)}
                )
                await send_tracked({"type": "http.response.body", "body": body, "more_body": False})
                return
            response = await backend.handle(request)
            if response.stream is None:
                await send_tracked(
                    {"type": "http.response.start", "status": response.status, "headers": _send_headers(response.headers)}
                )
                await send_tracked({"type": "http.response.body", "body": response.body, "more_body": False})
                return
            # Pull the first frame before sending the head: if every candidate fails
            # before the first byte, the client gets a normal JSON error (503/502)
            # instead of a 200 that immediately dies.
            stream = response.stream
            try:
                first_chunk = await stream.__anext__()
            except StopAsyncIteration:
                first_chunk = None
            except NormalizedError as error:
                await _send_json_error(send_tracked, map_upstream_error(error))
                return
            except NoProviderAvailable:
                await _send_json_error(send_tracked, ErrorCode.no_free_provider)
                return
            await send_tracked(
                {"type": "http.response.start", "status": response.status, "headers": _send_headers(response.headers)}
            )
            if first_chunk is not None:
                await send_tracked({"type": "http.response.body", "body": first_chunk, "more_body": True})
            async for chunk in stream:
                await send_tracked({"type": "http.response.body", "body": chunk, "more_body": True})
            await send_tracked({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            fields = {
                "method": str(scope.get("method", "")),
                "path": str(scope.get("path", "/"))[:MAX_LOGGED_PATH],
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
            if info["status"] is not None:
                fields["status"] = info["status"]
            if info["request_id"] is not None:
                fields["request_id"] = info["request_id"]
            obs.log_event("http_request", **fields)

    return app
