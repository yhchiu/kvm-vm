import json
import os
from pathlib import Path
import grp
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/monitoring/central/writers.py'
HOST = '2f3c4e13-46e4-44ce-ae61-2886cb3ba8cc'
PASSWORD = 'example-private-password-12345'


@pytest.fixture
def managed(tmp_path):
    hosts = tmp_path / 'hosts.json'
    hosts.write_text(json.dumps([dict(host_id=HOST, host='a', site='tpe', enabled=True)]))
    directory = tmp_path / 'writers'
    def call(*args, password=None):
        return subprocess.run([sys.executable, str(SCRIPT), '--directory', str(directory), '--hosts', str(hosts), *args],
                              input=password, capture_output=True, text=True, timeout=15)
    assert call('init', grp.getgrgid(os.getegid()).gr_name).returncode == 0
    return directory, hosts, call


@pytest.mark.skipif(not shutil.which('htpasswd'), reason='requires apache2-utils')
def test_lifecycle_and_live_snapshot(managed):
    directory, hosts, call = managed
    path = directory / 'htpasswd'
    name = call('stage', HOST).stdout.strip()
    second = call('stage', HOST).stdout.strip()
    assert name.startswith(HOST + '.') and name != second
    assert call('activate', name, password=PASSWORD+'\n').returncode == 0
    old = path.read_bytes()
    assert PASSWORD.encode() not in old
    assert subprocess.run(['htpasswd','-vb',str(path),name,PASSWORD], capture_output=True).returncode == 0
    assert call('activate', second, password=PASSWORD+'\n').returncode != 0
    assert call('rotate', name, password='short\n').returncode != 0
    assert path.read_bytes() == old
    assert call('rotate', name, password=PASSWORD+'new\n').returncode == 0
    assert subprocess.run(['htpasswd','-vb',str(path),name,PASSWORD], capture_output=True).returncode != 0
    assert call('check').returncode == 0
    # Inventory disable is maintenance, not authentication revocation.
    rows = json.loads(hosts.read_text()); rows[0]['enabled'] = False
    hosts.write_text(json.dumps(rows))
    assert call('check').returncode == 0
    hosts.unlink()
    assert call('check').returncode != 0
    assert call('revoke', name).returncode == 0
    assert call('revoke', name).returncode == 0
    assert call('activate', name, password=PASSWORD+'\n').returncode != 0
    assert call('activate', second, password=PASSWORD+'\n').returncode != 0
    hosts.write_text(json.dumps(rows))
    assert call('activate', second, password=PASSWORD+'\n').returncode == 0
    records = json.loads(call('list').stdout)
    assert set(r['history'][-1]['action'] for r in records) == {'activate','revoke'}
    assert '$2' not in call('list').stdout
    assert path.stat().st_mode & 0o777 == 0o640
    assert directory.stat().st_mode & 0o777 == 0o750


def test_corrupt_snapshot_and_unknown_hosts_fail_closed(managed):
    directory, _, call = managed
    path = directory / 'htpasswd'
    assert call('stage','00000000-0000-0000-0000-000000000000').returncode != 0
    path.write_text('# kvm-vm managed writers v1\nrogue:hash\n')
    before = path.read_bytes()
    assert call('stage',HOST).returncode != 0
    assert call('init',grp.getgrgid(os.getegid()).gr_name).returncode != 0
    assert path.read_bytes() == before
    path.unlink(); path.symlink_to('/dev/null')
    assert call('list').returncode != 0


def test_managed_nginx_config_pins_route_and_strips_client_query():
    text = (SCRIPT.parent/'nginx-managed-writers.example.conf').read_text()
    assert 'auth_basic_user_file /etc/nginx/kvm-vm-writers/htpasswd;' in text
    assert 'satisfy all;' in text and 'open_file_cache off;' in text
    assert 'proxy_set_header Authorization "";' in text
    assert 'proxy_pass http://127.0.0.1:8428/api/v1/write?extra_label=host_id=$kvm_vm_bound_host;' in text
    assert '$args' not in text and '$request_uri' not in text


@pytest.mark.skipif(not shutil.which('nginx') or not shutil.which('htpasswd'), reason='optional local nginx and apache2-utils required')
def test_real_nginx_auth_and_fixed_upstream(managed, tmp_path):
    """Real nginx, mock loopback receiver. Does NOT prove VM label deduplication."""
    import base64
    import http.client
    import http.server
    import socket
    import threading
    import time

    directory, _, call = managed
    name = call('stage', HOST).stdout.strip()
    assert call('activate', name, password=PASSWORD+'\n').returncode == 0
    seen = []
    class Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, self.headers.get('Authorization')))
            self.rfile.read(int(self.headers.get('Content-Length', 0)))
            self.send_response(204); self.end_headers()
        def log_message(self, *_): pass
    try:
        receiver = http.server.HTTPServer(('127.0.0.1', 0), Receiver)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    except OSError:
        pytest.skip('loopback bind unavailable')
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    template = (SCRIPT.parent/'nginx-managed-writers.example.conf').read_text()
    map_config = template[template.index('map $remote_user'):template.index('# The following')]
    server = '\n'.join(line[2:] for line in template.splitlines() if line.startswith('# ') and
                       (line.startswith('#     ') or line == '# server {' or line == '# }'))
    server = '\n'.join(line for line in server.splitlines() if not any(k in line for k in ('ssl_certificate','server_name')))
    server = server.replace('listen 443 ssl;', f'listen 127.0.0.1:{port};').replace('/etc/nginx/kvm-vm-writers/htpasswd', str(directory/'htpasswd')).replace('127.0.0.1:8428', f'127.0.0.1:{receiver.server_port}')
    # Isolated test worker runs with test UID so temporary paths are readable.
    # No changes to system nginx, no public listeners, no real credentials.
    user = 'user root;\n' if os.geteuid() == 0 else ''
    config = tmp_path/'nginx.conf'
    config.write_text(user + f'pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\nevents {{}}\nhttp {{\naccess_log off;\n' + map_config + server + '\n}\n')
    args = ['nginx','-p',str(tmp_path)+'/', '-c',str(config),'-g','daemon off;']
    checked = subprocess.run(args+['-t'], capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0, checked.stderr
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def request(method='POST', path='/api/v1/write', user=name, password=PASSWORD):
        headers = {} if user is None else {'Authorization': 'Basic '+base64.b64encode((user+':'+password).encode()).decode()}
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        try:
            connection.request(method,path,body=b'data',headers=headers)
            response = connection.getresponse(); response.read(); return response.status
        finally:
            connection.close()
    try:
        for _ in range(40):
            try:
                request(user=None); break
            except OSError: time.sleep(.1)
        else: pytest.fail('nginx did not start')
        assert request(user=None) in (401,403)
        assert request(password='wrong') == 401
        assert request(method='GET') == 405
        assert request(path='/api/v1/query') == 404
        assert request(path='/api/v1/write?extra_label=host_id=forged') == 204
        assert seen == [(f'/api/v1/write?extra_label=host_id={HOST}', None)]
        assert call('revoke', name).returncode == 0
        assert request() == 401
        assert len(seen) == 1
    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
        receiver.shutdown(); receiver.server_close(); thread.join(timeout=5)
