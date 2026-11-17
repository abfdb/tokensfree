import unittest, json, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from server import Gateway, make_server
P = {'model':'tokensfree-auto','messages':[{'role':'user','content':'private-prompt-marker'}]}
class IdempotencyTests(unittest.TestCase):
 def setUp(self): self.g=Gateway()
 def test_retry_reuses_response_without_extra_attempts(self):
  a=self.g.dispatch(P,'req-1'); count=len(self.g.events)
  self.assertEqual(a,self.g.dispatch(P,'req-1')); self.assertEqual(count,len(self.g.events))
 def test_different_content_conflicts(self):
  self.g.dispatch(P,'req-1'); q=P|{'messages':[{'role':'user','content':'different'}]}
  self.assertEqual(self.g.dispatch(q,'req-1')[0],409)
 def test_parallel_retries(self):
  with ThreadPoolExecutor(max_workers=8) as pool: r=list(pool.map(lambda _:self.g.dispatch(P,'same-key'),range(20)))
  self.assertTrue(all(x==r[0] for x in r));self.assertEqual(len(self.g.events),2)
 def test_failure_cached_even_after_reset(self):
  self.g.reset('exhausted'); result=self.g.dispatch(P,'req-1');self.g.reset('success')
  self.assertEqual(result,self.g.dispatch(P,'req-1'));self.assertEqual(len(self.g.events),0)
 def test_response_cannot_mutate_cache(self):
  r=self.g.dispatch(P,'req-1');r[1]['model']='bad'
  self.assertEqual(self.g.dispatch(P,'req-1')[1]['model'],'mock-beta')
 def test_expiry(self):
  with patch('server.time.monotonic',return_value=1):a=self.g.dispatch(P,'req-1')
  with patch('server.time.monotonic',return_value=302):b=self.g.dispatch(P,'req-1')
  self.assertNotEqual(a[1]['id'],b[1]['id'])
 def test_bounded_cache(self):
  for i in range(100):self.g.dispatch(P,f'req-{i}')
  count=len(self.g.events)
  self.assertEqual(self.g.dispatch(P,'new-key')[0],503)
  self.assertEqual(len(self.g.events),count);self.assertEqual(len(self.g.cache),100)
  self.assertEqual(self.g.dispatch(P,'req-0')[0],200)
 def test_ttl_starts_after_completion(self):
  with patch('server.time.monotonic',side_effect=[1,302]):self.g.dispatch(P,'req-1')
  self.assertEqual(self.g.cache['req-1'][0],602)
 def test_invalid_key(self):
  for key in ('','a'*129,'bad key','中文',1):
   with self.assertRaises(ValueError):self.g.dispatch(P,key)
 def test_no_plaintext_in_cache_or_status(self):
  self.g.dispatch(P,'req-1')
  self.assertNotIn('private-prompt-marker',repr(self.g.cache));self.assertNotIn('private-prompt-marker',json.dumps(self.g.snapshot()))
 def test_header_integration(self):
  srv=make_server(0);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
  try:
   url=f'http://127.0.0.1:{srv.server_port}/v1/chat/completions'
   def request():
    req=urllib.request.Request(url,data=json.dumps(P).encode(),headers={'Authorization':'Bearer '+srv.token,'Idempotency-Key':'client-request-1'})
    with urllib.request.urlopen(req) as r:return json.load(r)
   self.assertEqual(request(),request());self.assertEqual(len(srv.gateway.events),2)
  finally:srv.shutdown();srv.server_close();thread.join()
if __name__=='__main__':unittest.main()
