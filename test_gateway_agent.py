"""Opt-in integrated fixture tests. No DSH installation or external services."""
import http.client,json,threading,unittest
from pathlib import Path
from server import make_server
from agent_fixture import CALL_ID,SENTINEL,expected_read_result

def sse_json_frames(body):
    """Parse SSE data: frames into dicts; skip the [DONE] sentinel."""
    frames=[]
    for line in body.decode('utf-8').splitlines():
        if not line.startswith('data: '):continue
        payload=line[len('data: '):].strip()
        if payload=='[DONE]':continue
        frames.append(json.loads(payload))
    return frames

def start():
    return {'model':'tokensfree-fixture','stream':True,'messages':[{'role':'user','content':'fixture'}],
            'tools':[{'type':'function','function':{'name':'read'}}]}

class GatewayAgentTests(unittest.TestCase):
    def setUp(self):
        self.srv=make_server(0, enable_agent_fixture=True)
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.srv.shutdown();self.srv.server_close();self.thread.join()
    def request(self,payload=None,*,path='/v1/chat/completions',token=True,method='POST'):
        c=http.client.HTTPConnection('127.0.0.1',self.srv.server_port,timeout=3)
        headers={'Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+self.srv.token
        try:
            c.request(method,path,None if payload is None else json.dumps(payload).encode(),headers)
            r=c.getresponse();return r.status,r.getheader('Content-Type'),r.read()
        finally:c.close()
    def test_factory_public_fixture_and_catalog(self):
        self.assertEqual(Path(self.srv.fixture_path).read_text(),SENTINEL+'\n')
        status,_,body=self.request(path='/v1/models',method='GET')
        self.assertEqual(status,200)
        self.assertIn('tokensfree-fixture',[x['id'] for x in json.loads(body)['data']])
    def test_default_disabled(self):
        srv=make_server(0);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            c=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=3)
            c.request('POST','/v1/chat/completions',json.dumps(start()).encode(),{'Authorization':'Bearer '+srv.token})
            r=c.getresponse();self.assertEqual(r.status,400);r.read();c.close()
            self.assertFalse(getattr(srv,'enable_agent_fixture',False))
        finally:srv.shutdown();srv.server_close();t.join()
    def test_enabled_sse_roundtrip(self):
        status,mime,body=self.request(start())
        self.assertEqual(status,200);self.assertTrue(mime.startswith('text/event-stream'))
        self.assertIn(b'data: [DONE]',body);self.assertIn(CALL_ID.encode(),body)
        payload={'model':'tokensfree-fixture','stream':True,'messages':[{'role':'tool','tool_call_id':CALL_ID,
                 'content':expected_read_result(str(self.srv.fixture_path))}]}
        status,_,body=self.request(payload)
        self.assertEqual(status,200);self.assertIn(b'fixture tool roundtrip verified',body)
        self.assertTrue(self.srv.roundtrip.result_verified)
        self.assertEqual(self.srv.roundtrip.dispatches,1)
        self.assertEqual(self.request(payload)[0],400)
    def test_auth_before_dispatch(self):
        self.assertEqual(self.request(start(),token=False)[0],401)
        self.assertEqual(self.srv.roundtrip.dispatches,0)
    def test_reset_cannot_reissue(self):
        self.assertEqual(self.request(start())[0],200)
        self.assertEqual(self.request({'scenario':'success'},path='/api/scenario')[0],200)
        self.assertEqual(self.request(start())[0],400)
        self.assertEqual(self.srv.roundtrip.dispatches,1)
    def test_concurrent_only_one_issuance(self):
        statuses=[]
        ts=[threading.Thread(target=lambda:statuses.append(self.request(start())[0])) for _ in range(5)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(statuses.count(200),1);self.assertEqual(statuses.count(400),4)
        self.assertEqual(self.srv.roundtrip.dispatches,1)
    def test_client_cannot_choose_path(self):
        payload=start();payload['fixture_path']='/etc/passwd'
        status,_,body=self.request(payload)
        if status==200:
            self.assertNotIn(b'/etc/passwd',body)
            frames=sse_json_frames(body)
            arguments=frames[1]['choices'][0]['delta']['tool_calls'][0]['function']['arguments']
            self.assertEqual(json.loads(arguments)['file_path'],str(self.srv.fixture_path))
        else:self.assertEqual(status,400)



    def test_malformed_authorization_rejected_without_dispatch(self):
        for value in ['Bearer \xff', self.srv.token, 'Bearer']:
            c=http.client.HTTPConnection('127.0.0.1',self.srv.server_port,timeout=3)
            try:
                c.request('POST','/v1/chat/completions',json.dumps(start()).encode(),{'Authorization':value})
                response=c.getresponse();self.assertEqual(response.status,401);response.read()
            finally:c.close()
        self.assertEqual(self.srv.roundtrip.dispatches,0)

if __name__=='__main__':unittest.main()
