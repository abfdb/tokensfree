"""Minimal local-only text client. Never accepts supplier keys or remote URLs."""
import http.client
import json
import uuid
import re

class LocalClient:
    def __init__(self, port=8765, timeout=5):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('invalid local port')
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
            raise ValueError('invalid timeout')
        self.port, self.timeout = port, timeout

    def _request(self, method, path, body=None, headers=None):
        # http.client avoids proxies, DNS resolution of attacker-supplied hosts,
        # redirect following and automatic retries. One request, one connection.
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            raw = response.read(262145)
            if len(raw) > 262144:
                raise ValueError('local response too large')
            if not 200 <= response.status < 300:
                raise RuntimeError('local gateway rejected request: ' + str(response.status))
            return json.loads(raw)
        finally:
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
        token = self._request('GET', '/api/session')['token']
        payload = {'model':'tokensfree-auto', 'messages':[{'role':'user','content':text}]}
        if allowed_providers is not None: payload['allowed_providers'] = allowed_providers
        return self._request('POST', '/v1/chat/completions', json.dumps(payload).encode(),
                             {'Authorization':'Bearer ' + token, 'Content-Type':'application/json',
                              'Idempotency-Key':key})

if __name__ == '__main__':
    print(LocalClient().complete('offline demo')['choices'][0]['message']['content'])
