"""Synthetic SSE tool roundtrip fixture. No real upstream or tool executor."""
import json, threading, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CALL_ID='call_tokensfree_read_once'
SENTINEL='TOKENSFREE_READ_FIXTURE_7f4c'

def expected_read_result(path):
    return f'<path>{path}</path>\n<type>file</type>\n<content>\n1: {SENTINEL}\n\n(End of file - total 1 lines)\n</content>'

class Roundtrip:
    def __init__(self, path):
        self.path=path; self.phase='new'; self.lock=threading.Lock()
        self.dispatches=0; self.result_verified=False
    def accept(self,payload):
        with self.lock:
            if not isinstance(payload,dict) or payload.get('stream') is not True or payload.get('model')!='tokensfree-fixture':
                raise ValueError('unsupported_request')
            messages=payload.get('messages')
            if not isinstance(messages,list) or not all(isinstance(x,dict) for x in messages): raise ValueError('invalid_messages')
            if self.phase=='new':
                if any(m.get('role')=='tool' for m in messages): raise ValueError('unexpected_result')
                tools=payload.get('tools',[])
                if not isinstance(tools,list): raise ValueError('invalid_tools')
                names=[t.get('function',{}).get('name') for t in tools if isinstance(t,dict) and t.get('type')=='function' and isinstance(t.get('function'),dict)]
                if 'read' not in names: raise ValueError('read_tool_not_offered')
                self.phase='awaiting';self.dispatches+=1
                return [({'role':'assistant'},None),({'tool_calls':[{'index':0,'id':CALL_ID,'type':'function','function':{'name':'read','arguments':json.dumps({'file_path':self.path,'offset':1,'limit':1})}}]},None),({},'tool_calls')]
            if self.phase=='awaiting':
                results=[m for m in messages if m.get('role')=='tool']
                if len(results)!=1 or results[0].get('tool_call_id')!=CALL_ID: raise ValueError('ambiguous_or_replayed_request')
                content=results[0].get('content')
                if set(results[0]) != {'role','tool_call_id','content'} or not isinstance(content,str) or content != expected_read_result(self.path):
                    raise ValueError('fixture_result_not_verified')
                self.result_verified=True;self.phase='complete'
                return [({'role':'assistant'},None),({'content':'fixture tool roundtrip verified'},None),({},'stop')]
            raise ValueError('fixture_already_completed')

def sse(events):
    chunks=[]
    for delta, finish in events:
        data={'id':'chatcmpl-fixture','object':'chat.completion.chunk','created':0,'model':'tokensfree-fixture','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
        chunks.append('data: '+json.dumps(data)+'\n\n')
    return (''.join(chunks)+'data: [DONE]\n\n').encode()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,status,body,mime='application/json'):
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers()
        try:
            if mime == 'text/event-stream':
                # Flush complete SSE events separately. State was consumed before
                # writing; a lost connection must not reissue the tool call.
                for frame in body.split(b'\n\n'):
                    if frame:
                        self.wfile.write(frame+b'\n\n');self.wfile.flush()
            else: self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):
            self.close_connection=True
    def do_POST(self):
        self.connection.settimeout(5)
        expected=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=expected or self.headers.get('Origin') is not None:
            return self.reply(403,b'{"error":"origin_rejected"}')
        if not secrets.compare_digest(self.headers.get('Authorization','').encode('utf-8'),('Bearer '+self.server.token).encode('utf-8')):
            return self.reply(401,b'{"error":"local_auth_required"}')
        if self.path!='/v1/chat/completions': return self.reply(404,b'{}')
        try:
            if self.headers.get('Transfer-Encoding') is not None: raise ValueError()
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)!=1: raise ValueError()
            n=int(lengths[0])
            if not 0<n<=262144: raise ValueError()
            payload=json.loads(self.rfile.read(n))
            events=self.server.roundtrip.accept(payload)
        except (ValueError,TypeError,TimeoutError,RecursionError):
            return self.reply(400,b'{"error":"fixture_request_rejected"}')
        self.reply(200,sse(events),'text/event-stream')

def make_fixture(path):
    srv=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    srv.token=secrets.token_urlsafe(32);srv.roundtrip=Roundtrip(str(path))
    return srv
