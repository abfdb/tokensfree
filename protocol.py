"""Offline OpenAI-shaped chat protocol fixture. NOT a live provider adapter.

Transport deliberately takes a local port, never an endpoint or credential.
It connects to literal 127.0.0.1, ignores proxies, follows no redirects and
uses only a fixed, meaningless fixture token. No account or billing evidence
is inferred from an HTTP response.
"""
from dataclasses import dataclass
import http.client
import json
import math
import socket
import time
import threading

MAX_BYTES = 65536
FIXTURE_TOKEN = 'tokensfree-offline-fixture-only'

@dataclass(frozen=True)
class ProtocolResult:
    outcome: str
    content: str = ''
    reason: str = ''


def encode_chat(messages, model='fixture-model', max_tokens=256):
    """Text-only, non-streaming subset. Reject unsupported fields, not ignore."""
    if not isinstance(model, str) or not model or len(model) > 128:
        raise ValueError('invalid model')
    if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
        raise ValueError('invalid output limit')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        raise ValueError('invalid messages')
    for message in messages:
        if not isinstance(message, dict) or set(message) != {'role', 'content'}:
            raise ValueError('unsupported message fields')
        if message['role'] not in ('system', 'user', 'assistant'):
            raise ValueError('unsupported role')
        if not isinstance(message['content'], str) or len(message['content']) > 4000:
            raise ValueError('invalid text')
    body = json.dumps(dict(model=model, messages=messages, stream=False,
                           max_tokens=max_tokens), ensure_ascii=False).encode('utf-8')
    if len(body) > MAX_BYTES:
        raise ValueError('request too large')
    return body


def decode_chat(status, body):
    """Never expose upstream error text (it may contain prompts or secrets)."""
    if status in (401, 403): return ProtocolResult('auth', reason='upstream_auth')
    if status == 429: return ProtocolResult('limited', reason='upstream_rate_limit')
    if status == 402: return ProtocolResult('blocked', reason='billing_required')
    if 300 <= status < 400: return ProtocolResult('blocked', reason='redirect_rejected')
    if status != 200: return ProtocolResult('unavailable', reason='upstream_http_error')
    try:
        if not isinstance(body, bytes) or len(body) > MAX_BYTES: raise ValueError()
        data = json.loads(body.decode('utf-8'))
        choices = data['choices']
        if not isinstance(choices, list) or len(choices) != 1: raise ValueError()
        choice = choices[0]
        message = choice['message']
        if message.get('refusal') or choice.get('finish_reason') == 'content_filter':
            return ProtocolResult('refusal', reason='upstream_refusal')
        if message.get('tool_calls') or message.get('function_call'):
            return ProtocolResult('blocked', reason='unsupported_tool_response')
        if message.get('role') != 'assistant': raise ValueError()
        if choice.get('finish_reason') not in ('stop', 'length'): raise ValueError()
        content = message['content']
        if not isinstance(content, str): raise ValueError()
        return ProtocolResult('success', content=content,
                              reason='truncated' if choice['finish_reason'] == 'length' else '')
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        return ProtocolResult('unavailable', reason='invalid_upstream_response')


class LoopbackFixtureAdapter:
    """For integration tests only; not selectable by production gateway.

    Only use a fixture you started yourself. An untrusted local service can
    read test prompts. Callers must never send private prompts or real keys.
    """
    def __init__(self, port, timeout=2.0):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid fixture port')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 10:
            raise ValueError('invalid fixture timeout')
        self.port, self.timeout = port, timeout

    def complete(self, messages, model='fixture-model', max_tokens=256):
        body = encode_chat(messages, model, max_tokens)
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        deadline = time.monotonic() + self.timeout
        timer = None
        try:
            connection.connect()
            transport_socket = connection.sock
            def expire():
                try: transport_socket.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError()
            timer = threading.Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            connection.request('POST', '/v1/chat/completions', body=body, headers={
                'Content-Type': 'application/json', 'Authorization': 'Bearer ' + FIXTURE_TOKEN,
                'Accept': 'application/json', 'Connection': 'close'})
            response = connection.getresponse()
            if response.status != 200:
                return decode_chat(response.status, b'')
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return ProtocolResult('unavailable', reason='invalid_content_type')
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                return ProtocolResult('unavailable', reason='unsupported_encoding')
            # read1 limits each blocking read; reset socket timeout to remaining
            # wall-clock budget so a slow body cannot extend the call forever.
            chunks, size = [], 0
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise TimeoutError()
                transport_socket.settimeout(remaining)
                chunk = response.read1(min(8192, MAX_BYTES + 1 - size))
                if not chunk: break
                size += len(chunk)
                if size > MAX_BYTES:
                    return ProtocolResult('unavailable', reason='response_too_large')
                chunks.append(chunk)
            return decode_chat(200, b''.join(chunks))
        except (OSError, TimeoutError, http.client.HTTPException):
            return ProtocolResult('unavailable', reason='transport_failed')
        finally:
            if timer is not None: timer.cancel()
            connection.close()
