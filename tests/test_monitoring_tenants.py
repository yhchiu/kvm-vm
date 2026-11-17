"""Tenant ownership is central operator metadata, NEVER customer query authorization."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
INV = ROOT / 'tools/monitoring/central/inventory/inventory.py'
CENTRAL = ROOT / 'tools/setup-central-monitoring.sh'
UUID = '6756e012-abcd-4123-9123-a2319180a577'


def inventory(path, *args):
    return subprocess.run([sys.executable, str(INV), '--file', str(path), *args],
                          capture_output=True, text=True, timeout=10)


def test_legacy_inventory_defaults_to_internal_without_rewriting(tmp_path):
    path = tmp_path / 'hosts.json'
    record = {'host_id': UUID, 'host': 'h01', 'site': 'taipei', 'enabled': True}
    path.write_text(json.dumps([record]))
    result = inventory(path, 'metrics')
    assert result.returncode == 0, result.stderr
    assert f'kvm_vm_host_expected{{host_id="{UUID}",host="h01",site="taipei"}} 1' in result.stdout
    assert f'kvm_vm_host_tenant_info{{host_id="{UUID}",tenant_id="internal"}} 1' in result.stdout
    assert json.loads(path.read_text()) == [record]  # legacy format stays untouched


def test_add_and_change_tenant_with_explicit_operator_command(tmp_path):
    path = tmp_path / 'hosts.json'
    assert inventory(path, 'add', UUID, 'h01', 'taipei', '--tenant', 'cust-001').returncode == 0
    assert inventory(path, 'add', UUID, 'duplicate', 'taipei').returncode != 0
    data = json.loads(path.read_text())
    assert data[0]['tenant_id'] == 'cust-001'
    assert inventory(path, 'set-tenant', UUID, 'cust-002').returncode == 0
    assert json.loads(path.read_text())[0]['tenant_id'] == 'cust-002'
    assert 'tenant_id="cust-002"' in inventory(path, 'metrics').stdout
    assert inventory(path, 'disable', UUID).returncode == 0
    assert f'kvm_vm_host_tenant_info{{host_id="{UUID}",tenant_id="cust-002"}} 1' in inventory(path, 'metrics').stdout
    assert 'kvm_vm_host_expected{' not in inventory(path, 'metrics').stdout
    assert inventory(path, 'remove', UUID).returncode == 0
    assert json.loads(path.read_text()) == []


@pytest.mark.parametrize('bad',["Tenant",'1234','../escape','bad/tenant','a"bad','a\nbad','a_bad','', 'a'*65])
def test_invalid_tenant_does_not_write_inventory(tmp_path, bad):
    path = tmp_path / 'hosts.json'
    assert inventory(path, 'add', UUID, 'h01', 'taipei', '--tenant', bad).returncode != 0
    assert not path.exists()
    assert inventory(path, 'add', UUID, 'h01', 'taipei').returncode == 0
    before = path.read_text()
    assert inventory(path, 'set-tenant', UUID, bad).returncode != 0
    assert path.read_text() == before


def test_inventory_unknown_fields_rejected_instead_of_silent_tenant_auth(tmp_path):
    path = tmp_path/'hosts.json'
    path.write_text(json.dumps([{'host_id': UUID, 'host': 'h01', 'site': 'taipei',
                                 'enabled': True, 'tenant_id': 'cust-001', 'can_query': True}]))
    assert inventory(path, 'metrics').returncode != 0


def test_central_customer_access_is_not_enabled_by_grafana_filtering():
    compose = yaml.safe_load((ROOT / 'tools/monitoring/central/compose.yaml').read_text())
    assert compose['services']['grafana']['environment']['GF_AUTH_ANONYMOUS_ENABLED'] == 'false'
    assert compose['services']['grafana']['environment']['GF_USERS_ALLOW_SIGN_UP'] == 'false'
    for service in ('grafana','victoriametrics'):
        assert all(str(port).startswith('127.0.0.1:') for port in compose['services'][service]['ports'])


def test_customer_readiness_must_fail_closed(tmp_path):
    cfg = tmp_path / 'central'
    first = subprocess.run([str(CENTRAL), 'render', str(cfg)], capture_output=True, text=True, timeout=10)
    assert first.returncode == 0, first.stderr
    env={**os.environ, 'KVM_VM_CENTRAL_DIR':str(cfg)}
    proc=subprocess.run([str(CENTRAL), 'customer-readiness'],env=env,capture_output=True,text=True,timeout=10)
    assert proc.returncode != 0
    assert 'BLOCKED' in proc.stdout
    assert 'VictoriaMetrics Single' in proc.stdout


@pytest.mark.skipif(os.geteuid() != 0, reason='operator CLI requires root')
def test_tenant_assignment_blocks_older_running_inventory_code(tmp_path):
    cfg = tmp_path / 'central'
    first = subprocess.run([str(CENTRAL), 'render', str(cfg)], capture_output=True, text=True, timeout=10)
    assert first.returncode == 0, first.stderr
    env = {**os.environ, 'KVM_VM_CENTRAL_DIR': str(cfg)}
    old = cfg / 'inventory/inventory.py'
    old.write_text('# old version without tenant support\n')
    proc = subprocess.run([str(CENTRAL), 'inventory', 'add', UUID, 'h01', 'dc1', '--tenant', 'cust-001'],
                          env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0
    assert 'sync-templates' in proc.stderr
    assert json.loads((cfg / 'inventory/hosts.json').read_text()) == []
    proc = subprocess.run([str(CENTRAL), 'inventory', 'set-tenant', UUID, 'cust-001'],
                          env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode != 0
    assert 'sync-templates' in proc.stderr
