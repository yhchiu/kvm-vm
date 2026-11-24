"""Contract self-tests: deliberately vulnerable adapters must never pass.

The fake is NOT a backend acceptance result or production security code.
"""
import json
import sys
from pathlib import Path
import pytest

DIR = Path(__file__).resolve().parent / 'tenancy'
sys.path.insert(0, str(DIR))
try:
    from contract import Contract, Reply, Blocked, SCOPES
    from http_adapter import HTTPAdapter, remote_write, private_json
finally:
    sys.path.remove(str(DIR))


class Fake:
    name = 'contract-self-test-only'

    def __init__(self, defect=None):
        self.defect = defect
        self.rows = {s:[] for s in SCOPES}
        self.revoked = self.reader_revoked = self.moved = self.down = False
        self.actions = []

    def control(self, action, fixture):
        self.actions.append(action)
        self.fixture = fixture
        if action == 'prepare' and self.defect == 'setup-fail':
            raise RuntimeError('secret must not appear')
        if action == 'transfer': self.moved = True
        if action == 'revoke': self.revoked = True
        if action == 'revoke-reader': self.reader_revoked = True
        if action == 'policy-down': self.down = True
        if action == 'policy-up': self.down = False
        if action == 'cleanup' and self.defect == 'cleanup-fail': raise RuntimeError('secret')

    def attacks(self):
        return {'read':[{'headers':{'X-Scope-OrgID':'b'}},{'path':'/select/b/prometheus/api/v1/query'}], 'write':[{'headers':{'X-Scope-OrgID':'b'}},{'path':'/insert/b/prometheus/api/v1/write'},{'params':{'extra_label':'tenant_id=b'}}]}

    def write(self, principal, samples, attack=None):
        if principal == 'host1-old' and self.defect=='rotation': principal='host1'
        if principal not in ('host1','host2') and self.defect != 'auth': return Reply(401)
        if principal == 'host1' and self.revoked and self.defect != 'revocation': return Reply(401)
        if self.down and self.defect != 'fail-open': return Reply(503)
        f = self.fixture
        for s in samples:
            labels = dict(s.labels)
            vm = labels.get('vm_id')
            owner_host = 'host2' if vm == f['vm_other'] else 'host1'
            if (principal != owner_host or vm == f['vm_unknown']) and self.defect not in ('spoof','auth'): continue
            scope = 'b' if vm == f['vm_b'] else 'a'
            if vm in (None,f['vm_internal']): scope = 'internal'
            if vm == f['vm_move'] and self.moved and (s.timestamp_ms >= f['transfer']*1000 or self.defect == 'late-transfer'): scope = 'b'
            if attack and self.defect == 'write-bypass': scope = 'b'
            for k in ('tenant_id','vm_account_id','vm_project_id'):
                if self.defect != 'tenant-label': labels.pop(k,None)
            if self.defect != 'host-label': labels['host_id'] = f[principal] if principal in ('host1','host2') else f['host1']
            row = {'labels':labels,'timestamp_ms':s.timestamp_ms,'value':s.value}
            if self.defect == 'drop-all': continue
            if self.defect == 'timestamp': row['timestamp_ms'] += 1000
            self.rows[scope].append(row)
            if self.defect == 'shared-storage': self.rows['b' if scope=='a' else 'a'].append(row)
        return Reply(204)

    def observe(self, scope, run_id, sample):
        if self.defect == 'oracle-down': return Reply(503)
        return Reply(200,[row for row in self.rows[scope] if row['labels']['probe']==sample.labels['probe']])

    def read(self, principal, endpoint, params, attack=None):
        if principal not in ('a','b') or (principal=='a' and self.reader_revoked and self.defect!='reader-revocation'): return Reply(401)
        if self.defect=='cache' or (attack and self.defect=='read-bypass'): principal = 'a' if not attack else 'b'
        rows = self.rows[principal]
        if self.defect=='read-leak' or (self.defect=='enumeration' and endpoint.startswith('label')):
            rows = self.rows['a'] + self.rows['b'] + self.rows['internal']
        if endpoint=='labels': data = sorted({k for row in rows for k in row['labels']})
        elif endpoint.startswith('label/'):
            label = endpoint.split('/')[1]; data = sorted({row['labels'][label] for row in rows if label in row['labels']})
        elif endpoint=='series': data = [row['labels'] for row in rows]
        else: data = {'result':[{'metric':row['labels']} for row in rows]}
        return Reply(200,data)


def run(defect=None):
    fake = Fake(defect)
    report = Contract(fake,timeout=.003,quiet=.001,poll=.001).run()
    assert fake.actions[-1]=='cleanup'
    return report


def test_contract_passes_known_good_model():
    report = run()
    assert report['status']=='PASS',report
    assert len(report['results'])==10
    assert all(x['status']=='PASS' for x in report['results'])


@pytest.mark.parametrize('defect,scenario',[
    ('drop-all','mixed-host-batch'),('timestamp','mixed-host-batch'),('shared-storage','mixed-host-batch'),
    ('read-leak','read-isolation-cache-enumeration'),('cache','read-isolation-cache-enumeration'),
    ('enumeration','read-isolation-cache-enumeration'),('spoof','spoof-and-unknown'),
    ('tenant-label','spoof-and-unknown'),('host-label','spoof-and-unknown'),('read-bypass','api-bypass-and-role-separation'),
    ('write-bypass','api-bypass-and-role-separation'),('auth','spoof-and-unknown'),
    ('late-transfer','ownership-transfer-delayed-sample'),('fail-open','policy-outage-fail-closed'),
    ('rotation','writer-rotation'),('revocation','credential-revocation'),('reader-revocation','reader-revocation')])
def test_detects_intentionally_broken_security(defect,scenario):
    report=run(defect)
    assert report['status']=='FAIL',report
    assert any(x['scenario']==scenario and x['status']=='FAIL' for x in report['results']),report


@pytest.mark.parametrize('defect',['oracle-down','setup-fail','cleanup-fail'])
def test_missing_evidence_is_blocked_not_pass(defect):
    report=run(defect)
    assert report['status']=='BLOCKED',report
    assert 'secret' not in json.dumps(report)


def test_wire_format_contains_multiple_series():
    # Independently decode the Snappy literal and protobuf top-level framing
    # to prove a mixed batch actually contains multiple series.
    from contract import Sample
    import struct
    def read_varint(data,pos):
        n=shift=0
        while True:
            b=data[pos];pos+=1;n|=(b&127)<<shift
            if b<128:return n,pos
            shift+=7
    samples=[Sample({'__name__':'metric','vm':'x'},123456,1.25),Sample({'__name__':'metric','vm':'y'},123457,2.5)]
    data=remote_write(samples); length,pos=read_varint(data,0)
    tag=data[pos];pos+=1
    assert tag&3==0
    n=tag>>2
    if n>=60:
        width=n-59;n=int.from_bytes(data[pos:pos+width],'little');pos+=width
    raw=data[pos:];assert len(raw)==length==n+1
    for s in samples:
        key,i=read_varint(raw,0); size,j=read_varint(raw,i)
        assert key==10
        series=raw[j:j+size];raw=raw[j+size:]
        assert s.labels['vm'].encode() in series
        assert struct.pack('<d',s.value) in series
    assert not raw


def test_configuration_safety(tmp_path):
    f=tmp_path/'fixture.json';f.write_text('{}');f.chmod(0o644)
    with pytest.raises(Blocked): private_json(f)
    f.chmod(0o600);assert private_json(f)=={}
    with pytest.raises(Blocked): HTTPAdapter({'disposable':False})
    config={'disposable':True,'backend':'mimir','endpoints':{k:'http://127.0.0.1:8080' for k in ('write','read','oracle_a','oracle_b','oracle_internal')},'control_command':['/bin/true']}
    with pytest.raises(Blocked): HTTPAdapter(config)
    config['endpoints'].update(write='https://localhost:4443',read='https://localhost:4443')
    adapter=HTTPAdapter(config)
    with pytest.raises(Blocked): adapter.url('http://example.org','/api/v1/query')
    with pytest.raises(Blocked): adapter.url('https://user:pass@localhost','/api/v1/query')


def test_live_https_transport_and_redirect_rejection(tmp_path):
    """Exercise actual wire requests with TLS; server is not a metrics backend."""
    import shutil
    import subprocess
    import ssl
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from contract import Sample
    if not shutil.which('openssl'):
        pytest.skip('openssl not installed')
    cert, key = tmp_path/'cert.pem', tmp_path/'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1','-keyout',str(key),'-out',str(cert)], check=True, capture_output=True, timeout=15)
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            observed.append((self.path,self.headers['Authorization'],self.headers['Content-Encoding'],self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(204);self.end_headers()
        def do_GET(self):
            observed.append((self.path,self.headers['Authorization'],None,None))
            if self.path.startswith('/redirect'):
                self.send_response(302);self.send_header('Location','/forbidden');self.end_headers();return
            payload=json.dumps({'status':'success','data':{'resultType':'matrix','result':[{'metric':{'contract_run':'r','probe':'p'},'values':[[123.456,'3']]}]}}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        base=f'https://127.0.0.1:{server.server_port}'
        creds=tmp_path/'credentials.json';creds.write_text(json.dumps({p:{'bearer':p+'-synthetic'} for p in ('host1','reader-a','oracle-a')}));creds.chmod(0o600)
        config={'disposable':True,'backend':'mimir','ca_file':str(cert),'endpoints':{k:base+'/prefix' for k in ('write','read','oracle_a','oracle_b','oracle_internal')},'credentials_file':str(creds),'control_command':['/bin/true']}
        adapter=HTTPAdapter(config)
        samples=[Sample({'__name__':'metric','vm_id':'a'},123456,3),Sample({'__name__':'metric','vm_id':'b'},123457,4)]
        assert adapter.write('host1',samples).status==204
        assert observed[-1]==('/prefix/api/v1/write','Bearer host1-synthetic','snappy',remote_write(samples))
        assert adapter.read('a','query',{'query':'metric'},attack={'path':'/redirect'}).status==302
        assert observed[-1][0].startswith('/redirect?')
        assert len(observed)==2  # no follow, no credential forwarding
        reply=adapter.observe('a','r',Sample({'probe':'p'},123456,3))
        assert reply.data==[{'labels':{'contract_run':'r','probe':'p'},'timestamp_ms':123456,'value':3.0}]
        assert observed[-1][1]=='Bearer oracle-a-synthetic'
        with pytest.raises(Blocked): adapter.read('a','query',{'query':'metric'},attack={'params':{'query':'other'}})
        with pytest.raises(Blocked): adapter.read('a','query',{},attack={'headers':{'Authorization':'wrong'}})
        with pytest.raises(Blocked): adapter.read('a','query',{},attack={'path':'//example.com/path'})
        with pytest.raises(Blocked): HTTPAdapter({**config,'ca_file':None}).read('a','query',{})
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
