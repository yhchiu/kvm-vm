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
