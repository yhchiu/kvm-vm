"""Inventory, operator notification setup and optional host metrics tests.

All tests run offline, without a Docker daemon or a KVM host.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CENTRAL = ROOT / 'tools/setup-central-monitoring.sh'
INVENTORY = ROOT / 'tools/monitoring/central/inventory/inventory.py'
NOTICE = ROOT / 'tools/monitoring/central/configure-notifications.py'
CONFIGURE = ROOT / 'tools/monitoring/configure-remote.py'
UUID = 'eb90c4d9-7b0e-4f4f-a4bb-9490124c85ae'


def call(*parts, env=None):
    return subprocess.run([str(x) for x in parts], text=True, capture_output=True, env=env)


def create(tmp_path):
    cfg = tmp_path / 'monitoring center'
    result = call(CENTRAL, 'render', cfg)
    assert result.returncode == 0, result.stderr
    return cfg


def inventory(cfg, *args):
    return call(sys.executable, INVENTORY, '--file', cfg/'inventory/hosts.json', *args)


def test_inventory_new_central_seed_empty_and_repeated_render_preserves(tmp_path):
    cfg = create(tmp_path)
    assert json.loads((cfg/'inventory/hosts.json').read_text()) == []
    assert (cfg/'inventory/inventory.py').exists()
    assert (cfg/'inventory/scrape.yml').exists()
    assert (cfg/'alertmanager/secrets').is_dir()
    assert cfg.stat().st_mode & 0o777 == 0o700
    assert inventory(cfg,'add',UUID,'kvm-tpe-01','taipei').returncode == 0
    assert call(CENTRAL,'render',cfg).returncode == 0
    assert len(json.loads((cfg/'inventory/hosts.json').read_text())) == 1


def test_inventory_active_disabled_removed_and_labels(tmp_path):
    cfg = create(tmp_path)
    assert inventory(cfg,'add',UUID,'kvm-tpe-01','taipei').returncode == 0
    assert inventory(cfg,'add',UUID,'duplicate','taipei').returncode != 0
    actual = inventory(cfg,'metrics')
    assert actual.returncode == 0
    assert 'kvm_vm_host_expected{host_id="' + UUID + '",host="kvm-tpe-01",site="taipei"} 1' in actual.stdout
    assert inventory(cfg,'disable',UUID).returncode == 0
    assert 'kvm_vm_host_expected{' not in inventory(cfg,'metrics').stdout
    assert inventory(cfg,'enable',UUID).returncode == 0
    assert 'kvm_vm_host_expected{' in inventory(cfg,'metrics').stdout
    assert inventory(cfg,'remove',UUID).returncode == 0
    assert json.loads((cfg/'inventory/hosts.json').read_text()) == []


@pytest.mark.parametrize('name', ['abc"bad', 'abc\nbad', 'bad{', '測試主機', ''])
def test_inventory_rejects_unsafe_labels(tmp_path, name):
    cfg = create(tmp_path)
    assert inventory(cfg,'add',UUID,name,'site').returncode != 0
    assert json.loads((cfg/'inventory/hosts.json').read_text()) == []


def test_inventory_rejects_duplicate_corrupt_and_symlink(tmp_path):
    cfg = create(tmp_path)
    source = cfg/'inventory/hosts.json'
    assert inventory(cfg,'add','not-uuid','host','site').returncode != 0
    source.write_text('broken json')
    assert inventory(cfg,'metrics').returncode != 0
    source.unlink()
    secret = tmp_path/'outside'
    secret.write_text('never touch this')
    source.symlink_to(secret)
    assert inventory(cfg,'add',UUID,'host','site').returncode != 0
    assert secret.read_text() == 'never touch this'


def test_inventory_http_server_reads_updated_file(tmp_path):
    cfg = create(tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable,str(INVENTORY),'--file',str(cfg/'inventory/hosts.json'),
                                'serve','--bind','127.0.0.1','--port',str(port)],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics', timeout=.4) as result:
                    assert result.status == 200
                    assert 'text/plain' in result.headers['Content-Type']
                    assert b'# TYPE kvm_vm_host_expected gauge' in result.read()
                break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail('inventory HTTP listener did not start')
        assert inventory(cfg,'add',UUID,'host','site').returncode == 0
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics') as result:
            assert UUID.encode() in result.read()
        assert inventory(cfg,'disable',UUID).returncode == 0
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/metrics') as result:
            assert UUID.encode() not in result.read()
        assert urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz').read() == b'ok\n'
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f'http://127.0.0.1:{port}/private')
        assert e.value.code == 404
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_central_inventory_alert_matches_recent_samples_and_inventory(tmp_path):
    compose = yaml.safe_load((ROOT/'tools/monitoring/central/compose.yaml').read_text())
    assert 'inventory' in compose['services']
    assert 'ports' not in compose['services']['inventory']
    assert compose['services']['inventory']['read_only']
    assert './inventory:/inventory:ro' in compose['services']['inventory']['volumes']
    assert '-promscrape.config=/etc/victoriametrics/scrape.yml' in compose['services']['victoriametrics']['command']
    cfg = yaml.safe_load((ROOT/'tools/monitoring/central/inventory/scrape.yml').read_text())
    assert cfg['scrape_configs'][0]['job_name'] == 'kvm-vm-inventory'
    rules = yaml.safe_load((ROOT/'tools/monitoring/central/rules/rules.yml').read_text())
    offline = next(x for group in rules['groups'] for x in group['rules'] if x['alert']=='KvmVmHostOffline')
    assert 'kvm_vm_host_expected unless on(host_id)' in offline['expr']
    assert 'count_over_time(kvm_vm_collector_success[5m])' in offline['expr']
    assert offline['for'] == '5m'
    assert 'KvmVmInventoryScrapeFailed' in str(rules)


def test_notification_webhook_uses_secret_file_not_config(tmp_path):
    cfg = create(tmp_path)
    url_file = tmp_path/'webhook-url.txt'
    url='https://alerts.example.com/hook?token=testing-very-secret'
    url_file.write_text(url+'\n')
    result = call(sys.executable, NOTICE, cfg, 'webhook',url_file)
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load((cfg/'alertmanager/alertmanager.yml').read_text())
    assert config['route']['receiver'] == 'webhook'
    assert config['receivers'][0]['webhook_configs'][0]['url_file'] == '/etc/alertmanager/secrets/webhook-url'
    assert 'testing-very-secret' not in (cfg/'alertmanager/alertmanager.yml').read_text()
    assert url == (cfg/'alertmanager/secrets/webhook-url').read_text().strip()
    assert (cfg/'alertmanager/secrets/webhook-url').stat().st_mode & 0o777 == 0o644


def test_notification_webhook_rejects_http_and_symlink(tmp_path):
    cfg = create(tmp_path)
    url_file = tmp_path/'url'
    url_file.write_text('http://unsafe.example.com/post\n')
    assert call(sys.executable, NOTICE, cfg, 'webhook',url_file).returncode != 0
    assert 'unconfigured' in (cfg/'alertmanager/alertmanager.yml').read_text()
    url_file.write_text('https://alerts.example.com/hook\n')
    link = tmp_path/'link'
    link.symlink_to(url_file)
    assert call(sys.executable, NOTICE, cfg, 'webhook',link).returncode != 0
    assert 'unconfigured' in (cfg/'alertmanager/alertmanager.yml').read_text()


def test_notification_email_uses_password_file_and_tls(tmp_path):
    cfg = create(tmp_path)
    password_file = tmp_path/'smtp.secret'
    password_file.write_text('super-secret-app-password\n')
    result=call(sys.executable, NOTICE, cfg,'email','operator@example.com','smtp.example.com:587',
                'monitor@example.com','monitor@example.com',password_file)
    assert result.returncode == 0,result.stderr
    config = yaml.safe_load((cfg/'alertmanager/alertmanager.yml').read_text())
    email = config['receivers'][0]['email_configs'][0]
    assert email['require_tls'] and not email['force_implicit_tls']
    assert email['auth_password_file'] == '/etc/alertmanager/secrets/smtp-password'
    assert 'super-secret-app-password' not in (cfg/'alertmanager/alertmanager.yml').read_text()
    assert 'super-secret-app-password' in (cfg/'alertmanager/secrets/smtp-password').read_text()


def test_node_exporter_host_mode_agent_hybrid(tmp_path):
    remote = ROOT/'tools/monitoring/remote'
    for path in ('compose.agent.yaml','compose.hybrid.yaml'):
        compose=yaml.safe_load((remote/path).read_text())
        node=compose['services']['node_exporter']
        assert node['network_mode']=='host' and node['pid']=='host'
        assert 'ports' not in node
        assert '--web.listen-address=127.0.0.1:9100' in node['command']
        assert '/:/host:ro,rslave' in node['volumes']
    env={**os.environ,
         'KVM_VM_REMOTE_WRITE_URL':'https://metrics.example.com/api/v1/write',
         'KVM_VM_REMOTE_PASSWORD_FILE':str(tmp_path/'password')}
    (tmp_path/'password').write_text('pw1\n')
    result=call(CONFIGURE,tmp_path/'config',env=env)
    assert result.returncode==0,result.stderr
    for output in ('prometheus-hybrid.yml','vmagent-scrape.yml'):
        jobs=yaml.safe_load((tmp_path/'config'/output).read_text())['scrape_configs']
        node=next(x for x in jobs if x['job_name']=='node-exporter')
        assert node['static_configs'][0]['targets']==['127.0.0.1:9100']
    # Explicit reconfigure upgrades existing compose files; normal up preserves them.
    copy=tmp_path/'config/compose.agent.yaml'
    copy.write_text('custom: true\n')
    assert call(CONFIGURE,tmp_path/'config',env=env).returncode==0
    assert copy.read_text()=='custom: true\n'
    env['KVM_VM_REMOTE_RECONFIGURE']='1'
    assert call(CONFIGURE,tmp_path/'config',env=env).returncode==0
    assert 'node_exporter:' in copy.read_text()


def test_new_host_dashboard_and_alert_rules():
    dashboard=json.loads((ROOT/'tools/monitoring/central/grafana/dashboards/hosts.json').read_text())
    assert dashboard['uid']=='kvm-vm-hosts'
    assert [t['name'] for t in dashboard['templating']['list']]==['site','host']
    all_queries=' '.join(target['expr'] for p in dashboard['panels'] for target in p['targets'])
    assert 'node_memory_MemAvailable_bytes' in all_queries
    assert 'node_cpu_seconds_total' in all_queries
    assert 'kvm_vm_host_expected' in all_queries
    assert 'unless on(host_id)' in all_queries


@pytest.mark.skipif(os.geteuid()!=0,reason='root-only central scripts')
def test_central_operator_inventory_notifications_and_sync(tmp_path):
    cfg=create(tmp_path)
    env={**os.environ,'KVM_VM_CENTRAL_DIR':str(cfg)}
    assert call(CENTRAL,'inventory','add',UUID,'host01','taipei',env=env).returncode==0
    assert UUID in call(CENTRAL,'inventory','list',env=env).stdout
    config=cfg/'rules/rules.yml'
    config.write_text('groups: []\n')
    assert call(CENTRAL,'sync-templates',env=env).returncode==0
    assert 'KvmVmHostOffline' in config.read_text()
    assert UUID in (cfg/'inventory/hosts.json').read_text()
    backups=list((cfg/'backups').iterdir())
    assert len(backups)==1
    assert (backups[0]/'rules/rules.yml').read_text()=='groups: []\n'
    assert call(CENTRAL,'inventory','disable',UUID,env=env).returncode==0
    assert not json.loads((cfg/'inventory/hosts.json').read_text())[0]['enabled']
