"""Offline validation of the VictoriaMetrics/Grafana central stack."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import pytest
import yaml
ROOT=Path(__file__).resolve().parent.parent
CENTRAL=ROOT/'tools/setup-central-monitoring.sh'
CENTRAL_TEMPLATES=ROOT/'tools/monitoring/central'

def call(tool,*args,env=None):
    return subprocess.run([str(tool),*(str(a) for a in args)],text=True,capture_output=True,env=env)


def test_central_stack_not_public_by_default():
    compose=yaml.safe_load((CENTRAL_TEMPLATES/'compose.yaml').read_text())
    assert set(compose['services'])=={'victoriametrics','grafana','vmalert','alertmanager','inventory'}
    assert set(compose['volumes'])=={'vm_data','grafana_data'}
    for name in ('grafana','victoriametrics'):
        assert all(p.startswith('127.0.0.1:') for p in compose['services'][name]['ports'])
    assert 'ports' not in compose['services']['vmalert']
    assert 'ports' not in compose['services']['alertmanager']
    assert compose['services']['alertmanager']['image'] == 'prom/alertmanager:v0.34.2'
    ingress=(CENTRAL_TEMPLATES/'nginx-remote-write.example.conf').read_text()
    assert 'auth_basic_user_file' in ingress
    assert 'limit_except POST' in ingress


def test_central_render_preserves_data_and_settings(tmp_path):
    env = {**os.environ, 'GRAFANA_PORT':'3300','VM_PORT':'9438','VM_RETENTION':'60d'}
    cfg = tmp_path/'central cfg'
    first = call(CENTRAL, 'render', cfg, env=env)
    assert first.returncode == 0, first.stderr
    secret = (cfg / '.env').read_text()
    assert 'GRAFANA_PORT=3300' in secret
    assert 'VM_PORT=9438' in secret
    assert 'VM_RETENTION=60d' in secret
    assert 'GRAFANA_ADMIN_PASSWORD=' in secret
    assert (cfg / '.env').stat().st_mode & 0o777 == 0o600
    assert cfg.stat().st_mode & 0o777 == 0o700
    custom = cfg / 'rules/rules.yml'
    custom.write_text('groups: []\n')
    second = call(CENTRAL, 'render', cfg, env=env)
    assert second.returncode == 0
    assert (cfg / '.env').read_text() == secret
    assert custom.read_text() == 'groups: []\n'



@pytest.mark.parametrize('settings', [
    {'GRAFANA_PORT': '0'}, {'VM_PORT': 'x'}, {'VM_RETENTION': 'garbage'},
    {'VM_PORT': '3000'},
])
def test_central_rejects_invalid_first_run(tmp_path,settings):
    env = {**os.environ, **settings}
    assert call(CENTRAL, 'render', tmp_path / 'central', env=env).returncode != 0



def test_grafana_central_dashboard_includes_host_site_and_vm():
    folder = CENTRAL_TEMPLATES / 'grafana/dashboards'
    vms = json.loads((folder/'vms.json').read_text())
    assert vms['uid'] != 'kvm-vm-local'
    assert [v['name'] for v in vms['templating']['list']] == ['site','host','vm']
    exprs = '\n'.join(t['expr'] for p in vms['panels'] for t in p['targets'])
    assert 'site=~"$site"' in exprs and 'host=~"$host"' in exprs
    assert '{{site}}/{{host}}/{{vm}}' in json.dumps(vms)
    assert all(t['datasource']['uid']=='kvm-vm-victoriametrics' for p in vms['panels'] for t in p['targets'])
    fleet = json.loads((folder/'fleet.json').read_text())
    assert fleet['uid'] == 'kvm-vm-fleet'
    assert len(fleet['panels']) >= 5
    assert 'host_id' in json.dumps(fleet)
    assert 'vmagent' in (CENTRAL_TEMPLATES/'rules/rules.yml').read_text()
    for p in (folder/'vms.json',folder/'fleet.json'):
        assert json.loads(p.read_text())
    assert yaml.safe_load((CENTRAL_TEMPLATES/'rules/rules.yml').read_text())['groups']



@pytest.mark.skipif(os.geteuid()!=0,reason='Docker lifecycle mock requires root')
def test_central_lifecycle_and_bcrypt_enrollment_mock(tmp_path):
    fakebin=tmp_path/'mockbin'; fakebin.mkdir()
    log=tmp_path/'commands'
    docker=fakebin/'docker'
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$MOCK_LOG"
if [ "$1" = run ]; then
  # Stand-in bcrypt htpasswd output. Avoid fetching a real Docker image.
  echo 'kvm-tpe-01:$2y$05$examplebcrypthash'
fi
exit 0
''')
    docker.chmod(0o755)
    cfg=tmp_path/'central'
    env={**os.environ,'PATH':f'{fakebin}:{os.environ["PATH"]}',
         'KVM_VM_CENTRAL_DIR':str(cfg),'MOCK_LOG':str(log)}
    up=call(CENTRAL,'up',env=env)
    assert up.returncode==0,up.stderr
    first=(cfg/'.env').read_text()
    assert call(CENTRAL,'up',env=env).returncode==0
    assert (cfg/'.env').read_text()==first
    enrolled=call(CENTRAL,'enroll','kvm-tpe-01',env=env)
    assert enrolled.returncode==0,enrolled.stderr
    assert 'Password (shown once):' in enrolled.stdout
    assert '$2y$' in (cfg/'htpasswd').read_text()
    assert 'Password (shown once):' not in (cfg/'htpasswd').read_text()
    assert call(CENTRAL,'enroll','kvm-tpe-01',env=env).returncode!=0
    assert call(CENTRAL,'update',env=env).returncode==0
    assert call(CENTRAL,'reload',env=env).returncode==0
    assert '--force-recreate' in log.read_text()
    assert call(CENTRAL,'down',env=env).returncode==0
    assert (cfg/'.env').read_text()==first
    assert ' down' in log.read_text()
    assert ' down -v' not in log.read_text()
