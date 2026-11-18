"""ASGI 3.0 adapter over MemoryBackend. No third-party dependencies.

Any ASGI server (uvicorn, hypercorn) or a larger ASGI app can mount this callable.
It only translates the ASGI protocol; every decision stays in the backend contract.
"""
import json

from tokensfree.edge.backend import EdgeRequest
from tokensfree.edge.protocol import build_error
from tokensfree.errors import ErrorCode


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


def make_asgi_app(backend):
    if not hasattr(backend, "handle"):
        raise TypeError("backend must implement handle()")

    async def app(scope, receive, send):
        if scope.get("type") != "http":
            raise RuntimeError("only http scope is supported")
        try:
            request = await _read_request(scope, receive, backend.config.max_body_bytes)
        except BodyTooLarge:
            status, payload = build_error(ErrorCode.payload_too_large, "req-oversized")
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
            await send({"type": "http.response.start", "status": status, "headers": _send_headers(headers)})
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        response = await backend.handle(request)
        await send(
            {"type": "http.response.start", "status": response.status, "headers": _send_headers(response.headers)}
        )
        if response.stream is None:
            await send({"type": "http.response.body", "body": response.body, "more_body": False})
            return
        async for chunk in response.stream:
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    return app
