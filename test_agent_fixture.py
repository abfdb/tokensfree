import unittest,json,threading,http.client
from agent_fixture import Roundtrip,SENTINEL,CALL_ID,sse,make_fixture,expected_read_result

def start():return {'model':'tokensfree-fixture','stream':True,'messages':[{'role':'user','content':'fixture'}],'tools':[{'type':'function','function':{'name':'read'}}]}
def finish():return {'model':'tokensfree-fixture','stream':True,'messages':[{'role':'tool','tool_call_id':CALL_ID,'content':expected_read_result('/tmp/public-fixture')}]}

class AgentFixtureTest(unittest.TestCase):
    def test_roundtrip(self):
        r=Roundtrip('/tmp/public-fixture')
        first=r.accept(start()); self.assertEqual(first[-1][1],'tool_calls')
        self.assertEqual(json.loads(first[1][0]['tool_calls'][0]['function']['arguments'])['file_path'],'/tmp/public-fixture')
        last=r.accept(finish());self.assertEqual(last[-1][1],'stop')
        self.assertTrue(r.result_verified);self.assertEqual(r.dispatches,1)
    def test_replays_rejected(self):
        r=Roundtrip('/tmp/public-fixture');r.accept(start())
        with self.assertRaises(ValueError):r.accept(start())
        self.assertEqual(r.dispatches,1)
        r.accept(finish())
        with self.assertRaises(ValueError):r.accept(finish())
    def test_response_loss_does_not_reissue_tool(self):
        r=Roundtrip('/tmp/public-fixture')
        # Accept consumes the issuance before the HTTP response is written.
        # Discarding the response simulates disconnected/lost SSE delivery.
        sse(r.accept(start()))
        with self.assertRaises(ValueError):r.accept(start())
        self.assertEqual(r.dispatches,1)
        self.assertFalse(r.result_verified)
    def test_bad_result_rejected(self):
        for change in [{'tool_call_id':'wrong'},{'content':'wrong'},{'content':[SENTINEL]}]:
            r=Roundtrip('/tmp/public-fixture');r.accept(start());p=finish();p['messages'][0].update(change)
            with self.assertRaises(ValueError):r.accept(p)
            self.assertFalse(r.result_verified)
    def test_error_marker_is_not_success(self):
        for change in [{'content':'ERROR failed read '+SENTINEL,'is_error':True}, {'error':'failed'}, {'is_error':True}, {'content':SENTINEL}]:
            r=Roundtrip('/tmp/public-fixture');r.accept(start());p=finish();p['messages'][0].update(change)
            with self.assertRaises(ValueError):r.accept(p)
            self.assertFalse(r.result_verified)
    def test_unoffered_read_never_dispatches(self):
        r=Roundtrip('/tmp/public-fixture');p=start();p['tools']=[]
        with self.assertRaises(ValueError):r.accept(p)
        self.assertEqual(r.dispatches,0)
    def test_sse_shape(self):
        raw=sse(Roundtrip('/tmp/public-fixture').accept(start()))
        lines=raw.decode().splitlines();self.assertEqual(lines[-2],'data: [DONE]')
        for line in lines:
            if line and line!='data: [DONE]':self.assertEqual(json.loads(line[6:])['object'],'chat.completion.chunk')
    def test_concurrent_first_requests_issue_one_call(self):
        r=Roundtrip('/tmp/public-fixture');results=[]
        def call():
            try:r.accept(start());results.append(True)
            except ValueError:results.append(False)
        ts=[threading.Thread(target=call) for _ in range(6)]
        for t in ts:t.start()
        for t in ts:t.join()
        self.assertEqual(results.count(True),1);self.assertEqual(r.dispatches,1)
    def test_http_validation(self):
        srv=make_fixture('/tmp/public-fixture');t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            def request(headers,body=None,path='/v1/chat/completions'):
                c=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=2)
                try:
                    c.request('POST',path,json.dumps(start()).encode() if body is None else body,headers)
                    r=c.getresponse();raw=r.read();return r.status,raw
                finally:c.close()
            self.assertEqual(request({})[0],401)
            auth={'Authorization':'Bearer '+srv.token}
            self.assertEqual(request({**auth,'Origin':'https://evil.invalid'})[0],403)
            # Oversized body: declare the length and send no bytes so the server
            # rejects from the header alone (writing 256KiB raced with the close).
            c=http.client.HTTPConnection('127.0.0.1',srv.server_port,timeout=2)
            try:
                c.request('POST','/v1/chat/completions',b'',{**auth,'Content-Length':'262145'});r=c.getresponse();r.read()
                self.assertEqual(r.status,400)
            finally:c.close()
            self.assertEqual(request(auth,b'{')[0],400)
            self.assertEqual(request(auth,path='/v1/responses')[0],404)
            status,body=request(auth);self.assertEqual(status,200);self.assertIn(b'data: [DONE]',body)
            self.assertEqual(request(auth)[0],400);self.assertEqual(srv.roundtrip.dispatches,1)
        finally:srv.shutdown();srv.server_close();t.join()

if __name__=='__main__':unittest.main()
