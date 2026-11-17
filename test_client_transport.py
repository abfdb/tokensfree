"""Adversarial loopback HTTP fixtures only: no external calls or real secrets."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from client import LocalClient

class ClientTransportTests(unittest.TestCase):
    def setUp(self):
        self.mode='normal';self.body=b'{"ok":true}';self.status=200
        self.headers=[('Content-Type','application/json')]
        self.paths=[];owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self): self.respond()
            def do_POST(self): self.respond()
            def respond(self):
                owner.paths.append((self.command,self.path))
                try:
                    if owner.mode=='slow_header':
                        for byte in b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{}':
                            self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.045)
                        return
                    self.send_response(owner.status)
                    for key,value in owner.headers:self.send_header(key,value)
                    self.send_header('Location','/redirect-target')
                    if not any(k.lower()=='content-length' for k,v in owner.headers):
                        self.send_header('Content-Length',str(len(owner.body)))
                    self.end_headers()
                    if owner.mode=='slow_body':
                        for byte in owner.body:
                            self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.045)
                    else:self.wfile.write(owner.body)
                except (OSError,ValueError):pass
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=LocalClient(self.server.server_port,timeout=.18)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
    def get(self):return self.client._request('GET','/fixture')
    def assert_rejected(self):
        with self.assertRaises(Exception) as error:self.get()
        self.assertNotIn('secret-marker',str(error.exception))
    def test_normal_json(self):self.assertEqual(self.get(),{'ok':True})
    def test_slow_header_deadline_no_retry(self):
        self.mode='slow_header';started=time.monotonic();self.assert_rejected()
        self.assertLess(time.monotonic()-started,.8)
        self.assertEqual(self.paths,[('GET','/fixture')])
    def test_slow_body_deadline_no_retry(self):
        self.mode='slow_body';started=time.monotonic();self.assert_rejected()
        self.assertLess(time.monotonic()-started,.8)
        self.assertEqual(self.paths,[('GET','/fixture')])
    def test_redirect_not_followed(self):
        self.status=302;self.body=b'secret-marker';self.assert_rejected()
        self.assertEqual(self.paths,[('GET','/fixture')])
    def test_bad_content_type(self):
        self.headers=[('Content-Type','text/html')];self.assert_rejected()
    def test_bad_encoding(self):
        self.headers.append(('Content-Encoding','gzip'));self.assert_rejected()
    def test_error_body_redacted(self):
        self.status=500;self.body=b'secret-marker';self.assert_rejected()
    def test_duplicate_content_length(self):
        length=str(len(self.body));self.headers += [('Content-Length',length),('Content-Length',length)]
        self.assert_rejected()
    def test_truncated_content_length(self):
        self.headers.append(('Content-Length',str(len(self.body)+10)))
        self.assert_rejected()
    def test_oversize_response(self):
        self.body=b'x'*262145;self.assert_rejected()
    def test_duplicate_json_keys(self):
        self.body=b'{"token":"synthetic","token":"secret-marker"}';self.assert_rejected()
    def test_invalid_json_body(self):
        self.body=b'secret-marker';self.assert_rejected()
    def test_token_injection_never_posts(self):
        for token in ('bad\r\nInjected: secret-marker','secret-marker','',None,{},'a'*4096):
            with self.subTest(token_type=type(token).__name__):
                self.paths.clear();self.body=json.dumps({'token':token}).encode()
                with self.assertRaises(Exception) as error:self.client.complete('fixture')
                self.assertNotIn('secret-marker',str(error.exception))
                self.assertEqual(self.paths,[('GET','/api/session')])

if __name__=='__main__':unittest.main()
