"""Real TLS NGINX test against a synthetic loopback receiver, not a TSDB."""
import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.parse
import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT/'tools/monitoring/central/nginx-panel-query.example.conf'


@pytest.mark.skipif(not all(shutil.which(x) for x in ('nginx','openssl','htpasswd')),reason='optional nginx/openssl/apache2-utils')
def test_private_query_ingress(tmp_path):
    cert,key,auth=[tmp_path/x for x in ('cert.pem','key.pem','htpasswd')]
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)],check=True,capture_output=True,timeout=10)
    result=subprocess.run(['htpasswd','-niB','-C','4','panel'],input=b'test-panel-password\n',check=True,capture_output=True,timeout=10)
    auth.write_bytes(result.stdout)
    seen=[]
    class Upstream(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            seen.append((self.path,dict(self.headers)))
            body=json.dumps({'status':'success','data':{'resultType':'matrix','result':[]}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    try:
        upstream=http.server.ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    except OSError:
        pytest.skip('loopback socket unavailable')
    thread=threading.Thread(target=upstream.serve_forever,daemon=True);thread.start()
    text=TEMPLATE.read_text();block=text[text.index('# server {'):]
    server='\n'.join(line[2:] for line in block.splitlines() if line.startswith('# '))
    server=(server.replace('10.10.0.10:443',f'127.0.0.1:{port}')
            .replace('10.10.0.20','127.0.0.1').replace('/path/to/fullchain.pem',str(cert)).replace('/path/to/privkey.pem',str(key))
            .replace('/etc/nginx/kvm-vm-panel/htpasswd',str(auth)).replace('/var/log/nginx/kvm-vm-panel-query-error.log',str(tmp_path/'panel-error.log'))
            .replace('127.0.0.1:8428',f'127.0.0.1:{upstream.server_port}'))
    conf=tmp_path/'nginx.conf'
    conf.write_text(('user root;\n' if os.geteuid()==0 else '')+f'pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\nevents {{}}\nhttp {{\naccess_log off;\n{server}\n}}\n')
    cmd=['nginx','-p',str(tmp_path)+'/', '-c',str(conf),'-g','daemon off;']
    proc=None
    try:
        test=subprocess.run(cmd+['-t'],capture_output=True,text=True,timeout=10)
        assert test.returncode==0,test.stderr
        proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def call(path,method='GET',password='test-panel-password',source='127.0.0.1',extra=None):
            headers=dict(extra or {})
            if password is not None:headers['Authorization']='Basic '+base64.b64encode(('panel:'+password).encode()).decode()
            c=http.client.HTTPSConnection('127.0.0.1',port,context=ssl.create_default_context(cafile=str(cert)),timeout=3,source_address=(source,0))
            try:
                c.request(method,path,headers=headers);r=c.getresponse();r.read();return r.status,r.getheader('Cache-Control')
            finally:c.close()
        for _ in range(40):
            try:call('/api/v1/query_range',password=None);break
            except OSError:time.sleep(.05)
        else:pytest.fail('nginx did not start')
        path='/api/v1/query_range?'+urllib.parse.urlencode({'query':'metric{vm_id="example"}','start':100,'end':200,'step':15})
        assert call(path,password=None)[0]==401
        assert call(path,password='wrong')[0]==401
        assert call(path,source='127.0.0.2')[0]==403
        for endpoint in ('/api/v1/write','/api/v1/query','/api/v1/series','/api/v1/labels','/api/v1/admin/tsdb/delete_series','/api/v1/export','/select/1/prometheus/api/v1/query_range','/api/v1/query_range/','/metrics','/health'):
            assert call(endpoint)[0]==404
        for method in ('POST','PUT','DELETE','HEAD','OPTIONS'):
            assert call(path,method=method)[0]==405
        assert not seen
        assert call(path,extra={'X-Scope-OrgID':'forged','X-Forwarded-For':'127.0.0.9','X-HTTP-Method-Override':'POST'})==(200,'no-store')
        assert len(seen)==1 and seen[0][0]==path
        assert not {'authorization','x-scope-orgid','x-forwarded-for','x-http-method-override'} & {k.lower() for k in seen[0][1]}
        # Exercise the reference client over the same real TLS gateway.
        from test_panel_monitoring import m
        base=f'https://127.0.0.1:{port}'
        client=m.QueryClient(base,'panel','test-panel-password',ca_file=str(cert))
        assert client.range('up',100,200,15)==[]
        assert len(seen)==2
        assert 'nocache=1' in seen[-1][0]
        with pytest.raises(m.BackendError): m.QueryClient(base,'panel','test-panel-password').range('up',100,200,15)
        auth.write_text('')
        assert call(path)[0]==401
        assert len(seen)==2
    finally:
        if proc is not None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
        upstream.shutdown();upstream.server_close();thread.join(timeout=3)


def test_central_render_seeds_but_never_overwrites_query_template(tmp_path):
    tool=ROOT/'tools/setup-central-monitoring.sh';cfg=tmp_path/'central'
    for _ in range(2):
        subprocess.run(['bash',str(tool),'render',str(cfg)],check=True,capture_output=True,timeout=10)
        dest=cfg/TEMPLATE.name
        if _==0:
            assert dest.read_bytes()==TEMPLATE.read_bytes()
            dest.write_text('# local reviewed changes\n')
        else:assert dest.read_text()=='# local reviewed changes\n'
    assert 'nginx' not in (cfg/'compose.yaml').read_text()
