"""tokensfree: local, mock-only gateway. No outbound provider connections."""
import json, time, uuid, threading, secrets, argparse, hashlib, hmac, re, copy
from collections import OrderedDict
from pathlib import Path
from providers import ProviderSpec, MockAdapter, evaluate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCENARIOS = {'fallback': ['exhausted', 'success'], 'success': ['success', 'success'],
             'exhausted': ['exhausted', 'exhausted'], 'rate_limit': ['limited', 'success'],
             'auth_error': ['auth', 'success'], 'refusal': ['refusal', 'success'],
             'outage': ['unavailable', 'success'],
             'unknown_cost': ['success', 'success'], 'all_unknown_cost': ['success', 'success'],
             'expired_evidence': ['success', 'success']}

class Gateway:
    def __init__(self, authorized_providers=None):
        # Trusted constructor configuration, not client-supplied authorization.
        self.authorized_providers = frozenset(('mock-alpha', 'mock-beta') if authorized_providers is None else authorized_providers)
        if not self.authorized_providers.issubset({'mock-alpha', 'mock-beta'}):
            raise ValueError('unknown server provider')
        self.lock = threading.RLock()
        self.cache = OrderedDict()
        self.fingerprint_secret = secrets.token_bytes(32)
        self.events = []
        self.scenario = 'fallback'
        self.cooldowns = {}
    def reset(self, scenario):
        if scenario not in SCENARIOS: raise ValueError('unknown scenario')
        with self.lock:
            self.scenario = scenario
            self.cooldowns = {}
            self.events = []
    def dispatch(self, payload, idempotency_key=None):
        """Retry protection, not durable exactly-once execution.

        Cache responses (including failures) for five minutes, bounded to 100 keys.
        Store an HMAC rather than plaintext prompts. Scenario reset intentionally
        does not forget keys: an uncertain retry must not become a new operation.
        """
        if idempotency_key is None:
            return self.run(payload)
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', idempotency_key):
            raise ValueError('invalid idempotency key')
        serialized = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        fingerprint = hmac.new(self.fingerprint_secret, serialized, hashlib.sha256).digest()
        with self.lock:
            now = time.monotonic()
            expired = [key for key, entry in self.cache.items() if entry[0] <= now]
            for key in expired:
                del self.cache[key]
            if idempotency_key in self.cache:
                _, original, response = self.cache[idempotency_key]
                if not hmac.compare_digest(original, fingerprint):
                    return 409, {'error': {'code': 'idempotency_conflict', 'message': '同一请求键不能用于不同内容。请为新请求生成新键。'}}
                return copy.deepcopy(response)
            if len(self.cache) >= 100:
                return 503, {'error': {'code': 'idempotency_capacity', 'message': '去重缓存已满，请等待旧记录到期。请求未执行。'}}
            response = self.run(payload)
            self.cache[idempotency_key] = (time.monotonic() + 300, fingerprint, copy.deepcopy(response))
            return response

    def run(self, payload):
        if not isinstance(payload, dict): raise ValueError('request must be an object')
        if payload.get('model') != 'tokensfree-auto': raise ValueError('only tokensfree-auto is available')
        if payload.get('stream', False) is not False: raise ValueError('streaming not implemented')
        if any(k in payload for k in ('tools', 'functions', 'tool_choice')): raise ValueError('tool execution not implemented')
        messages = payload.get('messages')
        if not isinstance(messages, list) or not messages or len(messages) > 32: raise ValueError('messages must contain 1–32 text messages')
        for m in messages:
            if not isinstance(m, dict) or m.get('role') not in ('system','user','assistant') or not isinstance(m.get('content'), str): raise ValueError('text messages only')
            if len(m['content']) > 4000: raise ValueError('message too long')
        authorized = payload.get('allowed_providers', ['mock-alpha', 'mock-beta'])
        if not isinstance(authorized, list) or not authorized or any(not isinstance(x, str) or x not in ('mock-alpha', 'mock-beta') for x in authorized):
            raise ValueError('allowed_providers must be a nonempty subset of mock providers')
        with self.lock:
            request_id = 'mock-' + uuid.uuid4().hex[:12]
            trace = []
            for i, result in enumerate(SCENARIOS[self.scenario]):
                name = ['mock-alpha', 'mock-beta'][i]
                spec = self.provider_spec(name, i)
                decision = evaluate(spec, frozenset(authorized) & self.authorized_providers, time.time())
                if not decision.allowed:
                    result = 'policy:' + decision.reason
                elif self.cooldowns.get(name, 0) > time.time():
                    result = 'cooldown'
                else:
                    result = MockAdapter(result).complete(messages).value
                event = {'time': time.strftime('%H:%M:%S'), 'request_id': request_id, 'provider': name, 'result': result}
                self.events.append(event); self.events = self.events[-100:]; trace.append(event)
                if result == 'success':
                    return 200, {'id': request_id, 'object':'chat.completion', 'created':int(time.time()), 'model':name,
                        'choices':[{'index':0,'message':{'role':'assistant','content':f'[模拟响应] {name} 已接收文本请求。未调用真实 AI；切换流程已执行。'},'finish_reason':'stop'}],
                        'tokensfree':{'simulation':True,'trace':trace,'billing':'no external calls'}}
                if result in ('exhausted','auth'): self.cooldowns[name] = float('inf')
                if result == 'limited': self.cooldowns[name] = time.time()+30
                if result == 'unavailable': self.cooldowns[name] = time.time()+10
                if result == 'refusal':
                    return 422, {'error':{'code':'mock_safety_refusal','message':'模拟安全拒绝：停止，不通过切换规避。'},'trace':trace}
            return 503, {'error':{'code':'no_free_provider','message':'没有可用的模拟免费供应商。已停止，不回退付费。'},'trace':trace}
    def provider_spec(self, name, index):
        # Synthetic expiring evidence for policy tests, not platform verification.
        unknown = self.scenario == 'all_unknown_cost' or (self.scenario == 'unknown_cost' and index == 0)
        expired = self.scenario == 'expired_evidence' and index == 0
        return ProviderSpec(name, name, cost_evidence='unknown' if unknown else 'mock_zero_cost',
                            evidence_expires_at=time.time() - 1 if expired else time.time() + 60)

    def snapshot(self):
        with self.lock:
            providers = []
            for index, name in enumerate(('mock-alpha', 'mock-beta')):
                decision = evaluate(self.provider_spec(name, index), self.authorized_providers, time.time())
                state = 'blocked' if not decision.allowed else ('cooldown' if self.cooldowns.get(name,0)>time.time() else 'eligible')
                providers.append({'name':name, 'state':state, 'policy_reason':decision.reason, 'quota':'模拟；不是实际余额'})
            return {'simulation':True,'scenario':self.scenario,'events':list(self.events),'scenarios':list(SCENARIOS),'providers':providers}

ROOT = Path(__file__).parent/'static'
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass # Do not log request bodies or local access token.
    def send(self, code, body, mime='application/json; charset=utf-8'):
        raw = json.dumps(body, ensure_ascii=False).encode() if mime.startswith('application/json') else body
        self.send_response(code); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers(); self.wfile.write(raw)
    def allowed(self):
        hosts = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        return self.headers.get('Host') in hosts and (not self.headers.get('Origin') or self.headers['Origin'] in {'http://'+x for x in hosts})
    def do_GET(self):
        if not self.allowed(): return self.send(403,{'error':'Host or Origin rejected'})
        if self.path == '/api/session': return self.send(200,{'token':self.server.token})
        if self.path == '/api/status': return self.send(200,self.server.gateway.snapshot())
        if self.path == '/v1/models': return self.send(200,{'object':'list','data':[{'id':'tokensfree-auto','object':'model','owned_by':'mock-only'}]})
        paths={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/catalog/':'catalog/index.html','/catalog/app.js':'catalog/app.js','/catalog/data.js':'catalog/data.js','/catalog/style.css':'catalog/style.css'}
        file=paths.get(self.path)
        if not file: return self.send(404,{'error':'not found'})
        mime={'html':'text/html','js':'text/javascript','css':'text/css'}[file.rsplit('.',1)[1]]+'; charset=utf-8'
        self.send(200,(ROOT/file).read_bytes(),mime)
    def do_POST(self):
        if not self.allowed(): return self.send(403,{'error':'Host or Origin rejected'})
        token = self.headers.get('Authorization','').removeprefix('Bearer ')
        if not secrets.compare_digest(token,self.server.token): return self.send(401,{'error':'local session token required'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n<1 or n>65536: return self.send(413,{'error':'body size must be 1–65536 bytes'})
            self.connection.settimeout(5)
            payload=json.loads(self.rfile.read(n))
            if self.path == '/api/scenario':
                self.server.gateway.reset(payload.get('scenario')); return self.send(200,self.server.gateway.snapshot())
            if self.path == '/v1/chat/completions':
                status,data=self.server.gateway.dispatch(payload, self.headers.get('Idempotency-Key')); return self.send(status,data)
            return self.send(404,{'error':'not found'})
        except (ValueError, TypeError, AttributeError, TimeoutError): return self.send(400,{'error':'Invalid request or unsupported capability'})

def make_server(port=8765):
    srv=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    srv.gateway=Gateway(); srv.token=secrets.token_urlsafe(32)
    return srv
if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8765); parser.add_argument('--host',choices=['127.0.0.1'],default='127.0.0.1'); parser.add_argument('--strictPort',action='store_true'); args=parser.parse_args()
    server=make_server(args.port)
    print(f'tokensfree MOCK ONLY — http://127.0.0.1:{server.server_port} (Ctrl+C to stop)',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
