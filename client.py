"""Minimal local-only text client. Never accepts supplier keys or remote URLs."""
import http.client
import json
import uuid
import re
import socket
import threading
import time
import math

class LocalClient:
    def __init__(self, port=8765, timeout=5):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid local port')
        if type(timeout) not in (int, float) or not 0 < timeout <= 30 or not math.isfinite(timeout):
            raise ValueError('invalid timeout')
        self.port, self.timeout = port, timeout

    def _request(self, method, path, body=None, headers=None):
        # http.client avoids proxies, DNS resolution of attacker-supplied hosts,
        # redirect following and automatic retries. One request, one connection.
        deadline = time.monotonic() + self.timeout
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        timer = None
        try:
            conn.connect()
            sock = conn.sock
            def expire():
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('local request deadline exceeded')
            timer = threading.Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            if not 200 <= response.status < 300:
                # Do not read/echo untrusted error bodies or redirect locations.
                raise RuntimeError('local gateway rejected request: ' + str(response.status))
            if response.getheader('Content-Type','').split(';')[0].strip().lower() != 'application/json':
                raise ValueError('invalid local response content type')
            if response.getheader('Content-Encoding','identity') != 'identity':
                raise ValueError('unsupported local response encoding')
            lengths = response.headers.get_all('Content-Length', [])
            if len(lengths) > 1 or (lengths and response.getheader('Transfer-Encoding') is not None):
                raise ValueError('ambiguous local response framing')
            if lengths and (not lengths[0].isdigit() or not 0 <= int(lengths[0]) <= 262144):
                raise ValueError('local response too large or invalid')
            transfer = response.getheader('Transfer-Encoding')
            if transfer is not None and transfer.lower() != 'chunked':
                raise ValueError('unsupported local transfer encoding')
            raw = response.read(262145)
            if lengths and len(raw) != int(lengths[0]):
                raise ValueError('incomplete local response')
            if time.monotonic() >= deadline: raise TimeoutError('local request deadline exceeded')
            if len(raw) > 262144: raise ValueError('local response too large')
            def unique_pairs(items):
                obj = {}
                for key, value in items:
                    if key in obj: raise ValueError('duplicate JSON key')
                    obj[key] = value
                return obj
            try:
                return json.loads(raw, object_pairs_hook=unique_pairs,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise ValueError('invalid local JSON response') from None
        except (OSError, http.client.HTTPException):
            if time.monotonic() >= deadline:
                raise TimeoutError('local request deadline exceeded') from None
            raise RuntimeError('local transport failed') from None
        finally:
            if timer is not None: timer.cancel()
            conn.close()

    def complete(self, text, *, request_key=None, allowed_providers=None):
        """Reuse request_key for uncertain retries; no automatic retry is made."""
        if not isinstance(text, str) or len(text) > 4000:
            raise ValueError('text must be at most 4000 characters')
        if request_key is not None and (not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', request_key)):
            raise ValueError('invalid request key')
        # Exposed before any I/O so callers can reuse it after uncertain timeout.
        key = uuid.uuid4().hex if request_key is None else request_key
        self.last_request_key = key
        session = self._request('GET', '/api/session')
        token = session.get('token') if isinstance(session, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
            raise ValueError('invalid local session token')
        payload = {'model':'tokensfree-auto', 'messages':[{'role':'user','content':text}]}
        if allowed_providers is not None: payload['allowed_providers'] = allowed_providers
        return self._request('POST', '/v1/chat/completions', json.dumps(payload).encode(),
                             {'Authorization':'Bearer ' + token, 'Content-Type':'application/json',
                              'Idempotency-Key':key})

if __name__ == '__main__':
    print(LocalClient().complete('offline demo')['choices'][0]['message']['content'])
