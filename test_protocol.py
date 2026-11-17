import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from protocol import encode_chat, decode_chat, LoopbackFixtureAdapter, FIXTURE_TOKEN, MAX_BYTES

MESSAGES = [{'role': 'user', 'content': 'synthetic fixture input'}]
GOOD = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'fixture output'}, 'finish_reason': 'stop'}]}).encode()

class ProtocolTests(unittest.TestCase):
    def test_encode(self):
        result = json.loads(encode_chat(MESSAGES))
        self.assertFalse(result['stream'])
        self.assertEqual(result['max_tokens'], 256)
    def test_validation(self):
        for messages in ([], [{'role':'tool','content':'x'}], [{'role':'user','content':'x','tool_calls':[]}], [{'role':'user','content':[]}], [{'role':'user','content':'x'*4001}]):
            with self.assertRaises(ValueError): encode_chat(messages)
        for limit in (True, 0, 4097):
            with self.assertRaises(ValueError): encode_chat(MESSAGES, max_tokens=limit)
    def test_bytes_bound(self):
        with self.assertRaises(ValueError): encode_chat([{'role':'user','content':'鱼'*4000}]*10)
    def test_parse(self):
        self.assertEqual(decode_chat(200, GOOD).content, 'fixture output')
        self.assertEqual(decode_chat(200, GOOD.replace(b'stop', b'length')).reason, 'truncated')
    def test_errors_redacted(self):
        for code, outcome in [(401,'auth'), (403,'auth'), (429,'limited'), (402,'blocked'), (302,'blocked'), (500,'unavailable')]:
            result = decode_chat(code, b'private-key-and-prompt')
            self.assertEqual(result.outcome, outcome)
            self.assertNotIn('private', repr(result))
    def test_malformed(self):
        for data in (b'bad', b'null', b'[]', b'{}', b'\xff', b'x'*(MAX_BYTES+1)):
            self.assertEqual(decode_chat(200,data).reason,'invalid_upstream_response')
    def test_refusal_tools(self):
        for message, reason in [({'refusal':'no'},'upstream_refusal'), ({'tool_calls':[{}]},'unsupported_tool_response')]:
            body=json.dumps({'choices':[{'message':message}]}).encode()
            self.assertEqual(decode_chat(200,body).reason,reason)
    def test_no_endpoint_or_credentials(self):
        for port in ('https://example.com', True, 0, 65536):
            with self.assertRaises(ValueError): LoopbackFixtureAdapter(port)
        for timeout in (0,float('nan'),float('inf'),11, True):
            with self.assertRaises(ValueError): LoopbackFixtureAdapter(1234, timeout)

class HTTPTests(unittest.TestCase):
    def setUp(self):
        owner=self
        self.status,self.body,self.content_type=200,GOOD,'application/json'
        self.calls=[]
        self.delay=0
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.calls.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                if owner.delay: time.sleep(owner.delay)
                self.send_response(owner.status)
                self.send_header('Content-Type',owner.content_type)
                self.send_header('Content-Length',str(len(owner.body)))
                self.send_header('Location','https://example.invalid/never-follow')
                self.end_headers()
                try: self.wfile.write(owner.body)
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self,*args): pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.adapter=LoopbackFixtureAdapter(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def test_timeout(self):
        self.delay=0.2
        result=LoopbackFixtureAdapter(self.server.server_port,timeout=0.03).complete(MESSAGES)
        self.assertEqual(result.reason,'transport_failed')
    def test_roundtrip(self):
        self.assertEqual(self.adapter.complete(MESSAGES).content,'fixture output')
        self.assertEqual(self.calls[0][0],'/v1/chat/completions')
        self.assertEqual(self.calls[0][1],'Bearer '+FIXTURE_TOKEN)
        self.assertFalse(self.calls[0][2]['stream'])
    def test_redirect_not_followed(self):
        self.status=302
        self.assertEqual(self.adapter.complete(MESSAGES).reason,'redirect_rejected')
        self.assertEqual(len(self.calls),1)
    def test_limits(self):
        self.body=b'x'*(MAX_BYTES+1)
        self.assertEqual(self.adapter.complete(MESSAGES).reason,'response_too_large')
    def test_content_type(self):
        self.content_type='text/html'
        self.assertEqual(self.adapter.complete(MESSAGES).reason,'invalid_content_type')
    def test_remote_error(self):
        self.status=429;self.body=b'private details'
        self.assertEqual(self.adapter.complete(MESSAGES).reason,'upstream_rate_limit')

if __name__=='__main__': unittest.main()
