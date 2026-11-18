"""Shared fake upstream servers for loopback tests.

Not a test module: tests import SSEUpstream and configure status/body per case.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_SSE = (
    'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"he"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{"content":"llo"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _make_handler(owner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = owner.body.encode()
            self.send_response(owner.status)
            self.send_header("Content-Type", owner.content_type)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in owner.headers.items():
                self.send_header(name, value)
            self.end_headers()
            if owner.delay_s:
                time.sleep(owner.delay_s)
            self.wfile.write(payload)

    return Handler


class SSEUpstream:
    def __init__(self, body=DEFAULT_SSE, *, status=200, content_type="text/event-stream", headers=None, delay_s=0.0):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.headers = dict(headers or {})
        self.delay_s = float(delay_s)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._thread = None

    @property
    def port(self):
        return self._server.server_port

    def configure(self, *, body=None, status=None, content_type=None, headers=None):
        if body is not None:
            self.body = body
        if status is not None:
            self.status = status
        if content_type is not None:
            self.content_type = content_type
        if headers is not None:
            self.headers = dict(headers)

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(10)


class JsonUpstream:
    """Configurable JSON upstream that records request bodies (never logs prompt text)."""

    def __init__(self, body, *, status=200, content_type="application/json"):
        self.body = body
        self.status = status
        self.content_type = content_type
        self.requests = []
        self.headers = {}
        self.response_headers = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _make_json_handler(self))
        self._thread = None

    @property
    def port(self):
        return self._server.server_port

    def configure(self, *, body=None, status=None, content_type=None):
        if body is not None:
            self.body = body
        if status is not None:
            self.status = status
        if content_type is not None:
            self.content_type = content_type

    def start(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(10)


def _make_json_handler(owner):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                owner.requests.append(json.loads(raw.decode("utf-8")))
            except ValueError:
                owner.requests.append({})
            owner.headers = dict(self.headers.items())
            payload = owner.body.encode()
            self.send_response(owner.status)
            self.send_header("Content-Type", owner.content_type)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in owner.response_headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

    return Handler
