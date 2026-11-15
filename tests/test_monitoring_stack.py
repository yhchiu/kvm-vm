"""Offline safety and lifecycle checks for the Docker monitoring bootstrap."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'tools' / 'setup-monitoring.sh'
TEMPLATES = ROOT / 'tools' / 'monitoring'


def run(*args: str, env=None):
    return subprocess.run([str(SCRIPT), *args], text=True, capture_output=True, env=env)


def test_monitoring_yaml_is_valid_and_local_only():
    compose = yaml.safe_load((TEMPLATES / 'compose.yaml').read_text())
    assert compose['name'] == 'kvm-vm-monitoring'
    assert set(compose['services']) == {'prometheus', 'grafana'}
    for service in compose['services'].values():
        assert service['network_mode'] == 'host'
        assert 'ports' not in service
        assert service['restart'] == 'unless-stopped'
    cmd = ' '.join(compose['services']['prometheus']['command'])
    assert '--web.listen-address=127.0.0.1:' in cmd
    assert '--storage.tsdb.retention.time=' in cmd
    grafana_env = compose['services']['grafana']['environment']
    assert grafana_env['GF_SERVER_HTTP_ADDR'] == '127.0.0.1'
    assert grafana_env['GF_AUTH_ANONYMOUS_ENABLED'] == 'false'
    assert grafana_env['GF_SECURITY_ADMIN_PASSWORD'] != 'admin'
    assert grafana_env['GF_DS_PROMETHEUS_URL'].startswith('http://127.0.0.1:')
    prometheus = yaml.safe_load((TEMPLATES / 'prometheus.yml').read_text())
    assert prometheus['scrape_configs'][0]['static_configs'][0]['targets'] == ['127.0.0.1:9179']
    data = yaml.safe_load((TEMPLATES / 'grafana/provisioning/datasources/prometheus.yml').read_text())
    assert data['datasources'][0]['uid'] == 'kvm-vm-prometheus'
    assert data['datasources'][0]['url'] == '$GF_DS_PROMETHEUS_URL'
    assert set(compose['volumes']) == {'prometheus_data', 'grafana_data'}


def test_dashboard_only_uses_real_exporter_metric_names():
    dash = json.loads((TEMPLATES / 'grafana/dashboards/kvm-vm.json').read_text())
    assert dash['uid'] == 'kvm-vm-local'
    assert len(dash['panels']) >= 8
    assert dash['templating']['list'][0]['name'] == 'vm'
    source = (ROOT / 'kvm-vm').read_text()
    names = set(re.findall(r'"(kvm_vm_[a-z_]+)": \(', source))
    expressions = '\n'.join(t['expr'] for p in dash['panels'] for t in p['targets'])
    used = set(re.findall(r'kvm_vm_[a-z_]+', expressions))
    assert used and used <= names
    assert 'rate(' in expressions
    assert 'kvm_vm_domain_memory_assigned_bytes' in expressions
    assert 'kvm_vm_domain_memory_guest_used_bytes' in expressions


def test_offline_render_creates_private_secret_and_is_idempotent(tmp_path):
    target = tmp_path / 'monitoring configs'
    env = os.environ.copy()
    env.update(GRAFANA_PORT='3100', PROMETHEUS_PORT='9190', PROMETHEUS_RETENTION='30d')
    first = run('render', str(target), env=env)
    assert first.returncode == 0, first.stderr
    secret = target / '.env'
    assert secret.stat().st_mode & 0o777 == 0o600
    assert target.stat().st_mode & 0o777 == 0o700
    content = secret.read_text()
    assert 'GRAFANA_PORT=3100' in content
    assert 'PROMETHEUS_PORT=9190' in content
    assert 'PROMETHEUS_RETENTION=30d' in content
    match = re.search(r'^GRAFANA_ADMIN_PASSWORD=([0-9a-f]+)$', content, flags=re.M)
    assert match and len(match.group(1)) == 48
    custom = target / 'prometheus.yml'
    custom.write_text('custom_config: unchanged\n')
    second = run('render', str(target), env=env)
    assert second.returncode == 0, second.stderr
    assert secret.read_text() == content
    assert custom.read_text() == 'custom_config: unchanged\n'


@pytest.mark.parametrize('overrides', [
    {'GRAFANA_PORT': '0'},
    {'GRAFANA_PORT': '70000'},
    {'GRAFANA_PORT': 'abc'},
    {'GRAFANA_PORT': '9179'},
    {'PROMETHEUS_PORT': '3000'},
    {'PROMETHEUS_RETENTION': '15d;rm -rf /'},
])
def test_render_rejects_invalid_configuration(tmp_path, overrides):
    env = os.environ.copy()
    for key in ('GRAFANA_PORT', 'PROMETHEUS_PORT', 'PROMETHEUS_RETENTION'):
        env.pop(key, None)
    env.update(overrides)
    result = run('render', str(tmp_path / 'render'), env=env)
    assert result.returncode != 0
    assert 'ERROR:' in result.stderr


def test_help_is_safe_without_root():
    result = run('--help')
    assert result.returncode == 0
    assert 'Docker Compose' in result.stdout


@pytest.mark.skipif(os.geteuid() != 0, reason='mocked systemd lifecycle requires root')
def test_one_command_up_update_down_with_mocked_host(tmp_path):
    """No Docker/systemd access: all host commands are isolated mock binaries."""
    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir()
    log = tmp_path / 'host-commands.log'
    for cmd in ['docker', 'systemctl', 'virsh']:
        exe = fakebin / cmd
        exe.write_text('#!/bin/sh\nprintf "%s %s\\n" "'+cmd+'" "$*" >> "$MOCK_LOG"\nexit 0\n')
        exe.chmod(0o755)
    cli = fakebin / 'kvm-vm'
    cli.write_text('#!/bin/sh\n[ "$1" = exporter ] && [ "$2" = --help ]\n')
    cli.chmod(0o755)
    config = tmp_path / 'saved'
    systemd = tmp_path / 'systemd'
    env = os.environ.copy()
    env.update({
        'PATH': f'{fakebin}:{env["PATH"]}',
        'KVM_VM_MONITORING_DIR': str(config),
        'KVM_VM_MONITORING_SYSTEMD_DIR': str(systemd),
        'KVM_VM_MONITORING_CLI': str(cli),
        'MOCK_LOG': str(log),
    })
    first = run('up', env=env)
    assert first.returncode == 0, first.stderr
    unit = systemd / 'kvm-vm-monitoring-exporter.service'
    # If a real endpoint happened to be listening at 9179, it may be reused.
    if unit.exists():
        assert '127.0.0.1 --port 9179' in unit.read_text()
    secret = (config / '.env').read_text()
    again = run('up', env=env)
    assert again.returncode == 0, again.stderr
    assert (config / '.env').read_text() == secret
    upd = run('update', env=env)
    assert upd.returncode == 0, upd.stderr
    down = run('down', env=env)
    assert down.returncode == 0, down.stderr
    assert (config / '.env').read_text() == secret
    commands = log.read_text()
    assert 'docker compose --project-directory' in commands
    assert ' up -d' in commands
    assert ' pull' in commands
    assert ' down' in commands
    assert ' -v' not in commands  # volumes never removed
    if unit.exists():
        assert 'systemctl disable --now kvm-vm-monitoring-exporter.service' in commands
