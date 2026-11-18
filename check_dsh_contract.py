"""Offline HTTP contract probe, NOT a DSH client test. No external services."""
import json
import http.client
import threading
from server import make_server
from providers import MockAdapter


class CountingAdapter:
    """Records every outcome the gateway actually attempted. No network, no prompt retention."""

    calls = []

    def __init__(self, outcome):
        self.outcome = outcome

    def complete(self, messages):
        CountingAdapter.calls.append(self.outcome)
        return MockAdapter(self.outcome).complete(messages)


CASES = [
    ('models', 'GET', '/v1/models', None, 200),
    ('chat-text-only', 'POST', '/v1/chat/completions', {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'fixture'}]}, 200),
    ('chat-stream', 'POST', '/v1/chat/completions', {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'fixture'}], 'stream': True}, 400),
    ('chat-tools', 'POST', '/v1/chat/completions', {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'fixture'}], 'tools': []}, 400),
    ('chat-developer-role', 'POST', '/v1/chat/completions', {'model': 'tokensfree-auto', 'messages': [{'role': 'developer', 'content': 'fixture'}]}, 400),
    ('responses', 'POST', '/v1/responses', {'model': 'tokensfree-auto', 'input': 'fixture'}, 404),
    ('anthropic-messages', 'POST', '/v1/messages', {'model': 'tokensfree-auto', 'messages': [{'role': 'user', 'content': 'fixture'}], 'max_tokens': 16}, 404),
    ('gemini-generate', 'POST', '/v1beta/models/tokensfree-auto:generateContent', {'contents': [{'parts': [{'text': 'fixture'}]}]}, 404),
]


def probe(*, server_factory=make_server):
    server = server_factory(0, adapter_factory=CountingAdapter)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rows = []
        for name, method, path, payload, expected in CASES:
            server.gateway.reset('success')
            CountingAdapter.calls = []
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                raw = None if payload is None else json.dumps(payload).encode()
                conn.request(method, path, raw, {'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'})
                response = conn.getresponse()
                body = response.read(65537)
                attempts = len(CountingAdapter.calls)
                if len(body) > 65536: raise ValueError('response too large')
                parsed = json.loads(body)
                shape_ok = isinstance(parsed, dict) and (('data' in parsed if name == 'models' else 'choices' in parsed) if expected == 200 else 'error' in parsed)
                rows.append({'case': name, 'http_status': response.status, 'expected_status': expected,
                             'expected_behavior_verified': response.status == expected and shape_ok and (expected == 200 or attempts == 0),
                             'gateway_provider_attempts': attempts})
            finally:
                conn.close()
        return {'dsh_executed': False, 'external_calls': False, 'real_provider_enabled': False, 'cases': rows}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == '__main__':
    result = probe()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if all(r['expected_behavior_verified'] for r in result['cases']) else 1)
