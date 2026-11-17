"""Linux-only actual DSH probe in a new no-egress network namespace.
Install official @deepseek-ai/dsh@0.2.2-rc.1 separately with --ignore-scripts.
Usage: python experiments/dsh_offline_probe.py /absolute/path/to/dsh/lib/bin.js
Does not use existing DSH home, credentials, shell environment or workspace.
"""
import os,sys,subprocess,tempfile,pathlib,json

def main():
    if len(sys.argv) == 2:
        import shutil
        node = shutil.which('node')
        if node is None: raise SystemExit('node not available')
        # Failure to create a namespace aborts; never retry without isolation.
        return subprocess.call(['unshare','-Urn',sys.executable,__file__,'--isolated',str(pathlib.Path(sys.argv[1]).resolve()),node])
    if len(sys.argv) != 4 or sys.argv[1] != '--isolated': raise SystemExit('usage: probe PATH_TO_DSH_BIN')
    package = pathlib.Path(sys.argv[2]).parent.parent / 'package.json'
    metadata = json.loads(package.read_text())
    if metadata.get('name') != '@deepseek-ai/dsh' or metadata.get('version') != '0.2.2-rc.1':
        raise SystemExit('expected official DSH package 0.2.2-rc.1')
    # This internal mode is not a security boundary against a malicious caller.
    # Verify namespace has no configured non-loopback interfaces before running.
    import socket,fcntl,struct,threading
    if {line.split(':')[0].strip() for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]} != {'lo'}: raise SystemExit('network namespace not isolated')
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    fcntl.ioctl(sock,0x8914,struct.pack('16sh',b'lo',0x49));sock.close()
    sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
    from server import make_server
    srv=make_server(0)
    requests=[]
    statuses=[]
    original_send=srv.RequestHandlerClass.send
    def record_send(handler, code, body, mime='application/json; charset=utf-8'):
        statuses.append(code)
        return original_send(handler,code,body,mime)
    srv.RequestHandlerClass=type('ProbeHandler',(srv.RequestHandlerClass,),{'send':record_send})
    from unittest.mock import patch as mock_patch
    from providers import MockAdapter
    counter=mock_patch('server.MockAdapter.complete',autospec=True,side_effect=MockAdapter.complete)
    calls=counter.start()
    original_dispatch=srv.gateway.dispatch
    def capture(payload, key=None):
        requests.append({'stream':payload.get('stream'), 'tools_count':len(payload.get('tools',[])),
                         'message_roles':sorted({m.get('role','') for m in payload.get('messages',[])}),
                         'field_names':sorted(payload)})
        return original_dispatch(payload,key)
    srv.gateway.dispatch=capture
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='tokensfree-dsh-offline-') as root:
            home=pathlib.Path(root)/'home';home.mkdir()
            work=pathlib.Path(root)/'workspace';work.mkdir()
            patch=pathlib.Path(root)/'patch.yml'
            patch.write_text(f'''- id: agent-default-model
  config:
    provider: tokensfree-local
    model: tokensfree-auto
- id: llm-pi-ai
  config:
    providers:
      tokensfree-local:
        apiKeyEnv: TOKENSFREE_LOCAL_TOKEN
        api: openai-completions
        baseURL: http://127.0.0.1:{srv.server_port}/v1
        compat:
          supportsDeveloperRole: false
          maxTokensField: max_tokens
        models:
          - id: tokensfree-auto
- id: session-telemetry-otel
  disabled: true
''')
            env={'HOME':str(home),'DSH_HOME':str(home),'PATH':str(pathlib.Path(sys.argv[3]).parent)+':/usr/bin:/bin',
                 'TOKENSFREE_LOCAL_TOKEN':srv.token}
            try:
                result=subprocess.run([sys.argv[3],sys.argv[2],'--profile','headless','--patch',str(patch),'--json','Reply with the word fixture only. Do not call tools.'],
                                      cwd=work,env=env,capture_output=True,text=True,timeout=40)
                observed=bool(requests) and any(r['stream'] is True and r['tools_count'] > 0 for r in requests) and 400 in statuses and calls.call_count == 0
                print(json.dumps({'expected_rejection_observed':observed,'dsh_version':'0.2.2-rc.1','http_statuses':statuses,'adapter_calls':calls.call_count,'dsh_executed':True,'exit_code':result.returncode,'stdout':result.stdout.replace(srv.token,'[LOCAL_TOKEN_REDACTED]'),
                                  'stderr':result.stderr.replace(srv.token,'[LOCAL_TOKEN_REDACTED]'),'request_shapes':requests,'gateway_events':len(srv.gateway.events)},indent=2))
                return 0 if observed else 3
            except subprocess.TimeoutExpired:
                print(json.dumps({'dsh_executed':True,'timed_out':True,'request_shapes':requests,'gateway_events':len(srv.gateway.events)})); return 2
    finally:
        srv.shutdown();srv.server_close();thread.join();counter.stop()

if __name__=='__main__': raise SystemExit(main())
