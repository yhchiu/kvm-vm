"""Offline tests for Remote Write and local/agent/hybrid lifecycle."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import subprocess
import pytest
import yaml
ROOT=Path(__file__).resolve().parent.parent
LOCAL=ROOT / 'tools/setup-monitoring.sh'
CONFIGURE=ROOT / 'tools/monitoring/configure-remote.py'

def call(tool,*args,env=None):
    return subprocess.run([str(tool),*(str(a) for a in args)],text=True,capture_output=True,env=env)

def remote_env(tmp_path):
    pw = tmp_path / 'original-password'
    pw.write_text('test-credential-no-log\n')
    return {
        **os.environ,
        'KVM_VM_REMOTE_WRITE_URL': 'https://monitor.example.com/api/v1/write',
        'KVM_VM_REMOTE_USERNAME': 'host-01',
        'KVM_VM_REMOTE_PASSWORD_FILE': str(pw),
        'KVM_VM_MONITOR_HOST': 'host-taipei-01',
        'KVM_VM_MONITOR_SITE': 'taipei',
    }



def test_remote_config_has_stable_host_id_and_no_plaintext_secret(tmp_path):
    config = tmp_path / 'configs'
    env = remote_env(tmp_path)
    first = call(CONFIGURE, config, env=env)
    assert first.returncode == 0, first.stderr
    state = json.loads((config / 'remote.json').read_text())
    assert re.fullmatch('[0-9a-f-]{36}', state['host_id'])
    assert state['host'] == 'host-taipei-01'
    assert state['site'] == 'taipei'
    assert config.stat().st_mode & 0o777 == 0o700
    assert (config / 'remote.json').stat().st_mode & 0o777 == 0o600
    assert (config / 'remote.env').stat().st_mode & 0o777 == 0o600
    assert (config / 'remote-password').read_text().strip() == 'test-credential-no-log'
    agent = yaml.safe_load((config / 'vmagent-scrape.yml').read_text())
    labels = agent['scrape_configs'][0]['static_configs'][0]['labels']
    assert labels == {'host_id': state['host_id'], 'host': 'host-taipei-01', 'site': 'taipei'}
    assert {x['job_name'] for x in agent['scrape_configs']} == {'kvm-vm', 'vmagent'}
    hybrid = yaml.safe_load((config / 'prometheus-hybrid.yml').read_text())
    assert hybrid['global']['external_labels'] == labels
    assert hybrid['remote_write'][0]['basic_auth']['password_file'] == '/etc/prometheus/remote-password'
    for filename in ('remote.json','remote.env','vmagent-scrape.yml','prometheus-hybrid.yml',
                     'compose.agent.yaml','compose.hybrid.yaml'):
        assert 'test-credential-no-log' not in (config / filename).read_text()
    saved = (config / 'remote-password').stat().st_mtime_ns
    again = call(CONFIGURE, config, env=env)
    assert again.returncode == 0, again.stderr
    assert json.loads((config / 'remote.json').read_text()) == state
    assert (config / 'remote-password').stat().st_mtime_ns == saved



@pytest.mark.parametrize('url', [
    'http://monitor.example.com/api/v1/write',
    'https://user:pw@monitor.example.com/api/v1/write',
    'https://monitor.example.com/api/v1/write?token=secret',
    'https://monitor.example.com/metrics',
    'javascript:bad',
    'https://monitor.example.com/api/v1/write\nBAD=evil',
])
def test_remote_rejects_insecure_or_invalid_url(tmp_path, url):
    env = remote_env(tmp_path)
    env['KVM_VM_REMOTE_WRITE_URL'] = url
    result = call(CONFIGURE, tmp_path / 'config', env=env)
    assert result.returncode != 0
    assert 'ERROR' in result.stderr



def test_remote_rejects_credential_change_and_id_change(tmp_path):
    env = remote_env(tmp_path)
    cfg = tmp_path / 'config'
    assert call(CONFIGURE, cfg, env=env).returncode == 0
    (tmp_path / 'original-password').write_text('different\n')
    assert call(CONFIGURE, cfg, env=env).returncode != 0
    env.pop('KVM_VM_REMOTE_PASSWORD_FILE')
    env['KVM_VM_MONITOR_HOST_ID'] = 'some-other-host'
    assert call(CONFIGURE, cfg, env=env).returncode != 0



def test_remote_compose_security():
    remote = ROOT / 'tools/monitoring/remote'
    agent = yaml.safe_load((remote / 'compose.agent.yaml').read_text())
    assert list(agent['services']) == ['vmagent']
    service = agent['services']['vmagent']
    assert service['network_mode'] == 'host' and 'ports' not in service
    args = ' '.join(service['command'])
    assert '-remoteWrite.maxDiskUsagePerURL=' in args
    assert '-remoteWrite.basicAuth.passwordFile=' in args
    assert '-remoteWrite.tmpDataPath=' in args
    assert 'tlsInsecureSkipVerify' not in args
    assert 'vmagent_queue' in agent['volumes']
    hybrid = yaml.safe_load((remote / 'compose.hybrid.yaml').read_text())
    assert list(hybrid['services']) == ['prometheus']
    mounts = hybrid['services']['prometheus']['volumes']
    assert any('prometheus-hybrid.yml' in m and '/etc/prometheus/prometheus.yml:ro' in m for m in mounts)


@pytest.mark.skipif(os.geteuid() != 0, reason='systemd/Docker lifecycle mock is run as root')
def test_agent_lifecycle_mocks_without_docker(tmp_path):
    fakebin=tmp_path/'fakebin';fakebin.mkdir()
    log=tmp_path/'commands.txt'
    for name in ('docker','systemctl','virsh'):
        p=fakebin/name
        p.write_text(f'#!/bin/sh\nprintf "%s %s\\n" "{name}" "$*" >> "$MOCK_LOG"\nexit 0\n')
        p.chmod(0o755)
    cli=fakebin/'kvm-vm'
    cli.write_text('#!/bin/sh\n[ "$1" = exporter ] && [ "$2" = --help ]\n')
    cli.chmod(0o755)
    env=remote_env(tmp_path)
    env.update({
        'PATH':f'{fakebin}:{env["PATH"]}',
        'MOCK_LOG':str(log),
        'KVM_VM_MONITORING_DIR':str(tmp_path/'managed'),
        'KVM_VM_MONITORING_CLI':str(cli),
        'KVM_VM_MONITORING_SYSTEMD_DIR':str(tmp_path/'systemd'),
    })
    up=call(LOCAL,'up','--mode','agent',env=env)
    assert up.returncode==0,up.stderr
    cfg=tmp_path/'managed'
    assert (cfg/'.mode').read_text().strip()=='agent'
    assert (cfg/'remote.json').is_file()
    assert not (cfg/'compose.yaml').exists()  # no Prometheus/Grafana on agent host
    again=call(LOCAL,'up','--mode','agent',env=env)
    assert again.returncode==0,again.stderr
    refused=call(LOCAL,'up',env=env)
    assert refused.returncode!=0 and 'agent' in refused.stderr
    down=call(LOCAL,'down','--mode','agent',env=env)
    assert down.returncode==0,down.stderr
    assert not (cfg/'.mode').exists()
    assert (cfg/'remote-password').is_file()
    assert ' down' in log.read_text()
    assert ' -v' not in log.read_text()
    # Hybrid uses the original local installer, then the override.
    hybrid=call(LOCAL,'up','--mode','hybrid',env=env)
    assert hybrid.returncode==0,hybrid.stderr
    assert (cfg/'.mode').read_text().strip()=='hybrid'
    assert (cfg/'compose.yaml').is_file()
    assert (cfg/'prometheus-hybrid.yml').is_file()
    cmds=log.read_text()
    assert 'compose.hybrid.yaml' in cmds
    assert (cfg/'.env').is_file()
    assert call(LOCAL,'down','--mode','hybrid',env=env).returncode==0
    assert not (cfg/'.mode').exists()
    # Old local deployment remains available.
    assert call(LOCAL,'up',env=env).returncode==0




def test_remote_custom_scrape_preserved_until_explicit_reconfigure(tmp_path):
    env = remote_env(tmp_path)
    cfg = tmp_path / 'managed'
    assert call(CONFIGURE, cfg, env=env).returncode == 0
    custom = cfg / 'prometheus-hybrid.yml'
    custom.write_text('global: {scrape_interval: 20s}\n')
    assert call(CONFIGURE, cfg, env=env).returncode == 0
    assert custom.read_text() == 'global: {scrape_interval: 20s}\n'
    env['KVM_VM_REMOTE_RECONFIGURE'] = '1'
    assert call(CONFIGURE, cfg, env=env).returncode == 0
    assert 'remote_write' in custom.read_text()


def test_invalid_remote_configuration_never_persists_identity(tmp_path):
    env = remote_env(tmp_path)
    env['KVM_VM_REMOTE_WRITE_URL'] = 'http://unsafe.example.com/api/v1/write'
    cfg = tmp_path / 'managed'
    assert call(CONFIGURE, cfg, env=env).returncode != 0
    assert not (cfg / 'remote.json').exists()
