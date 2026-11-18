"""Minimal asyncio HTTP/1.1 host for the ASGI app. Loopback only.

Temporary host until D1 picks a server. It implements only what the gateway
needs: one request per connection, Content-Length request bodies, chunked
streaming responses, bounded sizes, no keep-alive.
"""
import asyncio
import json

from tokensfree.edge.protocol import build_error
from tokensfree.errors import ErrorCode

MAX_HEADER_BYTES = 32 * 1024
READ_TIMEOUT_S = 10.0
_REASONS = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


class BadRequest(Exception):
    pass


class PayloadTooLarge(Exception):
    pass


async def _read_request(reader, max_body):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), READ_TIMEOUT_S)
    except asyncio.LimitOverrunError:
        raise BadRequest() from None
    except asyncio.IncompleteReadError:
        raise BadRequest() from None
    if len(head) > MAX_HEADER_BYTES:
        raise BadRequest()
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise BadRequest()
    method, target, version = parts
    if version not in ("HTTP/1.1", "HTTP/1.0"):
        raise BadRequest()
    if not target.startswith("/"):
        raise BadRequest()
    path = target.split("?", 1)[0]
    headers = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator:
            raise BadRequest()
        key = name.strip().lower()
        if not key:
            raise BadRequest()
        clean = value.strip()
        headers[key] = headers[key] + ", " + clean if key in headers else clean
    if "transfer-encoding" in headers:
        raise BadRequest()
    try:
        length = int(headers.get("content-length", "0") or "0")
    except ValueError:
        raise BadRequest() from None
    if length < 0:
        raise BadRequest()
    if length > max_body:
        raise PayloadTooLarge()
    body = await asyncio.wait_for(reader.readexactly(length), READ_TIMEOUT_S) if length else b""
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(key.encode("latin-1"), value.encode("latin-1")) for key, value in headers.items()],
    }
    return scope, body


def _write_head(writer, status, headers):
    writer.write(("HTTP/1.1 %d %s\r\n" % (status, _REASONS.get(status, "Unknown"))).encode("latin-1"))
    for name, value in headers.items():
        writer.write((name + ": " + value + "\r\n").encode("latin-1"))
    writer.write(b"\r\n")


class AsgiHost:
    def __init__(self, app, *, host="127.0.0.1", port=8765, max_body=262144):
        if host != "127.0.0.1":
            raise ValueError("only loopback binding is supported")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("invalid port")
        if type(max_body) is not int or not 1024 <= max_body <= 4 * 1024 * 1024:
            raise ValueError("invalid max_body")
        self.app = app
        self.host = host
        self.port = port
        self.max_body = max_body
        self._server = None
        self._stop = None
        self.loop = None

    async def run_forever(self):
        self.loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=MAX_HEADER_BYTES + 4096)
        self.port = self._server.sockets[0].getsockname()[1]
        try:
            await self._stop.wait()
        finally:
            self._server.close()
            await self._server.wait_closed()

    def stop(self):
        if self.loop is not None and self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)

    async def _handle(self, reader, writer):
        try:
            try:
                scope, body = await _read_request(reader, self.max_body)
            except PayloadTooLarge:
                await self._write_json_error(writer, 413, ErrorCode.payload_too_large)
                return
            except BadRequest:
                await self._write_json_error(writer, 400, ErrorCode.invalid_request)
                return
            await self._invoke(scope, body, writer)
        except (ConnectionResetError, asyncio.IncompleteReadError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionResetError, OSError):
                pass

    async def _invoke(self, scope, body, writer):
        # All response state stays inside this call so concurrent connections cannot
        # clobber each other (the host instance is shared by every connection).
        pending = {}
        state = {"started": False, "streaming": False, "done": False}

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            kind = message.get("type")
            if kind == "http.response.start":
                status = message["status"]
                headers = {
                    name.decode("latin-1").lower(): value.decode("latin-1")
                    for name, value in message.get("headers", [])
                }
                headers.setdefault("connection", "close")
                pending.clear()
                pending.update(headers)
                state["status"] = status
                state["started"] = True
                return
            if kind != "http.response.body":
                raise RuntimeError("unexpected ASGI message")
            if not state["started"]:
                raise RuntimeError("body before start")
            payload = message.get("body", b"")
            more = bool(message.get("more_body", False))
            if not state["streaming"] and not more:
                pending["content-length"] = str(len(payload))
                _write_head(writer, state["status"], pending)
                writer.write(payload)
                state["done"] = True
                await writer.drain()
                return
            if not state["streaming"]:
                state["streaming"] = True
                pending["transfer-encoding"] = "chunked"
                _write_head(writer, state["status"], pending)
            if payload:
                writer.write(("%x\r\n" % len(payload)).encode("latin-1") + payload + b"\r\n")
            if not more:
                writer.write(b"0\r\n\r\n")
                state["done"] = True
            await writer.drain()

        try:
            await self.app(scope, receive, send)
        except Exception:
            # The app died before responding: answer 500 instead of dropping the socket.
            # Once the head is out we can only close, never start a second response.
            if not state["started"]:
                await self._write_json_error(writer, 500, ErrorCode.internal_error)
            return
        await writer.drain()

    async def _write_json_error(self, writer, status, code):
        status_text, payload = build_error(code, "req-host")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        writer.write(("HTTP/1.1 %d %s\r\n" % (status, _REASONS.get(status, "Error"))).encode("latin-1"))
        writer.write(b"content-type: application/json; charset=utf-8\r\n")
        writer.write(b"cache-control: no-store\r\n")
        writer.write(b"connection: close\r\n")
        writer.write(("content-length: %d\r\n" % len(body)).encode("latin-1"))
        writer.write(b"\r\n")
        writer.write(body)
        await writer.drain()


def main():
    import argparse
    import os

    from tokensfree.config import load_config
    from tokensfree.edge.asgi import make_asgi_app
    from tokensfree.edge.backend import MemoryBackend
    from tokensfree.runtime import Storage, build_loopback_streaming_runtime, build_mock_runtime, build_real_runtime

    parser = argparse.ArgumentParser(description="Serve the tokensfree ASGI app on loopback")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--loopback", type=int, default=None,
                        help="port of a local SSE fixture upstream; enables streaming instead of the mock runtime")
    parser.add_argument("--db", default=":memory:",
                        help="SQLite path for idempotency/ledger/events (default: in-memory, process lifetime)")
    parser.add_argument("--real", action="store_true",
                        help="use one real OpenAI-compatible provider configured by TOKENSFREE_REAL_* (still gated)")
    args = parser.parse_args()
    config = load_config(os.environ)
    if config.local_token is None:
        raise SystemExit("TOKENSFREE_LOCAL_TOKEN must be set for the new backend")
    storage = Storage.open(args.db)
    if args.real and args.loopback is not None:
        raise SystemExit("--real and --loopback are mutually exclusive")
    if args.real:
        try:
            runtime = build_real_runtime(config=config, env=os.environ, storage=storage)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        mode = "real provider %r model %r (enable_real=%s)" % (
            runtime.services.specs[0].name,
            runtime.services.specs[0].model,
            config.enable_real,
        )
    elif args.loopback is None:
        runtime = build_mock_runtime(config=config, storage=storage)
        mode = "mock runtime (no streaming provider)"
    else:
        runtime = build_loopback_streaming_runtime(args.loopback, config=config, storage=storage)
        mode = "loopback streaming runtime -> 127.0.0.1:%d" % args.loopback
    backend = MemoryBackend(config=config, services=runtime.services, port=args.port, token=config.local_token)
    host = AsgiHost(make_asgi_app(backend), port=args.port, max_body=config.max_body_bytes)
    print(
        "tokensfree ASGI backend: http://127.0.0.1:%d [%s] (db=%s, Ctrl+C to stop)" % (args.port, mode, args.db),
        flush=True,
    )
    try:
        asyncio.run(host.run_forever())
    except KeyboardInterrupt:
        host.stop()
    finally:
        storage.close()


if __name__ == "__main__":
    main()
