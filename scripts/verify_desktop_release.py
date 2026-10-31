"""Smoke-test the packaged desktop against a local fake model and disposable data."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request

from agent.agent_config import AgentConfigStore
from config import APP_VERSION, Settings

root=Path(__file__).resolve().parents[1]
exe=root/'dist'/f'MoreThanDone-{APP_VERSION}-windows-x64'/'MoreThanDone.exe'
class FakeModel(BaseHTTPRequestHandler):
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        finished=body['messages'][-1]['role']=='tool'
        delta={'content':'本地验收完成。'} if finished else {'tool_calls':[{'index':0,'id':'release-check','type':'function','function':{'name':'run_command','arguments':json.dumps({'command':'python -c "import sys; print(sys.executable); print(\'中文环境正常\')"'})}}]}
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
        for data in [{'id':'smoke','object':'chat.completion.chunk','created':0,'model':'fake','choices':[{'index':0,'delta':delta,'finish_reason':None}]},{'id':'smoke','object':'chat.completion.chunk','created':0,'model':'fake','choices':[{'index':0,'delta':{},'finish_reason':'stop' if finished else 'tool_calls'}]}]:
            self.wfile.write(('data: '+json.dumps(data,ensure_ascii=False)+'\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
    def log_message(self,*_):pass
model=ThreadingHTTPServer(('127.0.0.1',0),FakeModel)
threading.Thread(target=model.serve_forever,daemon=True).start()
with tempfile.TemporaryDirectory(prefix='mtd-011-smoke-', ignore_cleanup_errors=True) as temp:
    folder=Path(temp);workspace=folder/'workspace';workspace.mkdir()
    AgentConfigStore(folder/'agent_config.json',settings_loader=lambda:Settings(api_key='')).update({'workspace':str(workspace),'api_key':'disposable-test-key','base_url':f'http://127.0.0.1:{model.server_port}/v1','model':'fake','permission_mode':'full'})
    process=subprocess.Popen([str(exe),'--data-dir',str(folder),'--debug-log',str(folder/'debug.log')],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    port=None
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            command="$desktopIds = @(Get-CimInstance Win32_Process -Filter \"name = 'MoreThanDone.exe'\" | Where-Object { $_.ExecutablePath -eq '"+str(exe).replace("'","''")+"' } | Select-Object -ExpandProperty ProcessId); Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.OwningProcess -in $desktopIds -and $_.LocalAddress -eq '127.0.0.1' } | Select-Object -First 1 -ExpandProperty LocalPort"
            out=subprocess.run(['powershell','-NoProfile','-Command',command],capture_output=True,text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.strip()
            if out.isdigit():port=int(out);break
            time.sleep(.2)
        assert port, 'Desktop did not open its local server'
        base=f'http://127.0.0.1:{port}'
        def request(path,payload=None):
            req=urllib.request.Request(base+path,data=json.dumps(payload).encode() if payload is not None else None,headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=10) as reply:return json.load(reply)
        state=request('/api/workspace/state')
        assert state['projects'] == [] and state['active_project_id'] is None
        state=request('/api/workspace/project', {'name': '验证项目', 'workspace': str(workspace)})
        print('Packaged environment:',state['environment'])
        request('/api/workspace/chat',{'message':'验证本机 Python','conversation_id':state['active_conversation_id']})
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            state=request('/api/workspace/state')
            if state['status'] in ('idle','error'):break
            time.sleep(.1)
        results=[json.loads(e['result']) for e in state['events'] if e['kind']=='tool_result']
        assert state['status']=='idle' and results, state['status']
        result=results[-1]
        assert result['ok'] and '中文环境正常' in result['stdout'],result
        print('Packaged CMD/Python result: exit',result['returncode'],'; Unicode output OK; model round trip OK')
        request('/api/shutdown',{})
    finally:
        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        process.wait(timeout=10)
model.shutdown();model.server_close()
