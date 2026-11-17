"""Actual DSH + synthetic SSE fixture in a no-egress Linux namespace.
Usage: python experiments/dsh_tool_roundtrip.py /path/to/dsh/lib/bin.js
No real API, credentials, existing DSH home, or existing workspace are used.
"""
import sys,os,pathlib,json,subprocess,tempfile,threading

def main():
    if len(sys.argv)==2:
        import shutil
        node=shutil.which('node')
        if not node: raise SystemExit('node unavailable')
        return subprocess.call(['unshare','-Urn',sys.executable,__file__,'--isolated',str(pathlib.Path(sys.argv[1]).resolve()),node])
    if len(sys.argv)!=4 or sys.argv[1]!='--isolated': raise SystemExit('usage: probe DSH_BIN')
    metadata=json.loads((pathlib.Path(sys.argv[2]).parent.parent/'package.json').read_text())
    if metadata.get('name')!='@deepseek-ai/dsh' or metadata.get('version')!='0.2.2-rc.1': raise SystemExit('expected DSH 0.2.2-rc.1')
    if {line.split(':')[0].strip() for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]}!={'lo'}: raise SystemExit('network not isolated')
    import socket,fcntl,struct
    sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    fcntl.ioctl(sock,0x8914,struct.pack('16sh',b'lo',0x49));sock.close()
    sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
    from agent_fixture import CALL_ID
    from server import make_server
    with tempfile.TemporaryDirectory(prefix='tokensfree-dsh-tool-') as root:
        root=pathlib.Path(root);home=root/'home';home.mkdir();work=root/'workspace';work.mkdir()
        srv=make_server(0, enable_agent_fixture=True);thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
        try:
            patch=root/'patch.yml'
            patch.write_text(f'''- id: agent-default-model
  config:
    provider: tokensfree-local
    model: tokensfree-fixture
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
          - id: tokensfree-fixture
- id: session-telemetry-otel
  disabled: true
- id: session-title-llm
  disabled: true
''')
            env={'HOME':str(home),'DSH_HOME':str(home),'PATH':str(pathlib.Path(sys.argv[3]).parent)+':/usr/bin:/bin','TOKENSFREE_LOCAL_TOKEN':srv.token}
            try:
                result=subprocess.run([sys.argv[3],sys.argv[2],'--profile','headless','--patch',str(patch),'--json','Read the provided fixture using the read tool, then confirm completion. Do not use shell or any other tool.'],cwd=work,env=env,capture_output=True,text=True,timeout=40)
                events=[]
                for line in result.stdout.splitlines():
                    try: events.append(json.loads(line))
                    except ValueError: pass
                final=[e for e in events if e.get('type')=='final']
                final_ok=bool(final and final[-1].get('text')=='fixture tool roundtrip verified')
                event_types=[e.get('type') for e in events]
                one_tool_pair=event_types.count('tool_call')==1 and event_types.count('tool_result')==1
                verified=one_tool_pair and result.returncode==0 and srv.roundtrip.result_verified and srv.roundtrip.dispatches==1 and final_ok
                # Never print entire agent messages, prompts, tool results or stderr.
                print(json.dumps({'dsh_version':'0.2.2-rc.1','dsh_executed':True,'exit_code':result.returncode,'verified':verified,'tool':'read','call_id':CALL_ID,'dispatches':srv.roundtrip.dispatches,'marker_verified':srv.roundtrip.result_verified,'final_verified':final_ok,'event_types':event_types,'one_tool_pair':one_tool_pair,'phase':srv.roundtrip.phase},indent=2))
                return 0 if verified else 3
            except subprocess.TimeoutExpired:
                print(json.dumps({'timed_out':True,'dispatches':srv.roundtrip.dispatches,'marker_verified':srv.roundtrip.result_verified}));return 2
        finally: srv.shutdown();srv.server_close();thread.join()

if __name__=='__main__':raise SystemExit(main())
