import unittest, threading, json, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from server import Gateway, make_server
PAYLOAD={'model':'tokensfree-auto','messages':[{'role':'user','content':'test'}]}
class RoutingTests(unittest.TestCase):
 def setUp(self): self.g=Gateway()
 def test_fallback(self):
  code,data=self.g.run(PAYLOAD);self.assertEqual(code,200);self.assertEqual(data['model'],'mock-beta');self.assertEqual(len(data['tokensfree']['trace']),2)
 def test_fail_closed(self):
  self.g.reset('exhausted');self.assertEqual(self.g.run(PAYLOAD)[0],503)
 def test_refusal_no_fallback(self):
  self.g.reset('refusal');code,data=self.g.run(PAYLOAD);self.assertEqual(code,422);self.assertEqual(len(data['trace']),1)
 def test_auth_disables(self):
  self.g.reset('auth_error');self.g.run(PAYLOAD);self.assertEqual(self.g.run(PAYLOAD)[1]['tokensfree']['trace'][0]['result'],'cooldown')
 def test_rate_limit_cools(self):
  self.g.reset('rate_limit');self.g.run(PAYLOAD);self.assertEqual(self.g.run(PAYLOAD)[1]['tokensfree']['trace'][0]['result'],'cooldown')
 def test_unsupported(self):
  for extra in ({'stream':True},{'tools':[]},{'model':'paid-model'},{'messages':[]}):
   with self.assertRaises(ValueError):self.g.run(PAYLOAD|extra)
 def test_invalid_inputs(self):
  for value in (None,[],{'model':'tokensfree-auto','messages':[{'role':'user','content':{}}]}):
   with self.assertRaises(ValueError):self.g.run(value)
 def test_no_prompt_logging(self):
  self.g.run(PAYLOAD);self.assertNotIn('content',json.dumps(self.g.snapshot()))
 def test_parallel(self):
  with ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(lambda _:self.g.run(PAYLOAD),range(40)))
  self.assertTrue(all(c==200 for c,_ in results));self.assertLessEqual(len(self.g.snapshot()['events']),100)
class HttpTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.s=make_server(0);cls.url=f'http://127.0.0.1:{cls.s.server_port}';cls.t=threading.Thread(target=cls.s.serve_forever,daemon=True);cls.t.start()
 @classmethod
 def tearDownClass(cls):cls.s.shutdown();cls.s.server_close();cls.t.join()
 def request(self,path,body=None,headers=None):
  req=urllib.request.Request(self.url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers or {})
  try:r=urllib.request.urlopen(req)
  except urllib.error.HTTPError as e:r=e
  with r:return r.status,r.read()
 def test_auth_required(self):self.assertEqual(self.request('/v1/chat/completions',PAYLOAD)[0],401)
 def test_bad_origin(self):self.assertEqual(self.request('/api/session',headers={'Origin':'https://evil.example'})[0],403)
 def test_bad_host(self):self.assertEqual(self.request('/api/session',headers={'Host':'evil.example'})[0],403)
 def test_valid_route(self):
  token=json.loads(self.request('/api/session')[1])['token'];code,data=self.request('/v1/chat/completions',PAYLOAD,{'Authorization':'Bearer '+token});self.assertEqual(code,200);self.assertTrue(json.loads(data)['tokensfree']['simulation'])
 def test_static(self):
  for path in ['/','/app.js','/style.css','/catalog/','/catalog/data.js']:self.assertEqual(self.request(path)[0],200)
 def test_no_traversal(self):self.assertEqual(self.request('/../server.py')[0],404)
if __name__=='__main__':unittest.main()
