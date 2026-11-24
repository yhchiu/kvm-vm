"""Operator registry: temporal ownership, placement and safe persistence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import importlib.util
from unittest.mock import patch
from contextlib import redirect_stdout, redirect_stderr
import io
from concurrent.futures import ThreadPoolExecutor
import pytest

ROOT = Path(__file__).resolve().parent.parent
REG = ROOT / 'tools/monitoring/central/registry.py'
H1 = '11111111-1111-4111-8111-111111111111'
H2 = '22222222-2222-4222-8222-222222222222'
V1 = '33333333-3333-4333-8333-333333333333'
V2 = '44444444-4444-4444-8444-444444444444'


@pytest.fixture
def registry(tmp_path):
    hosts = tmp_path / 'hosts.json'
    hosts.write_text(json.dumps([{'host_id': x, 'host': n, 'site': 'dc1', 'enabled': True}
                                 for x, n in [(H1, 'h1'), (H2, 'h2')]]))
    file = tmp_path / 'private' / 'ownership.json'
    spec = importlib.util.spec_from_file_location('monitoring_registry', REG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def call(*args, ok=True, process=False):
        argv = [str(REG), '--file', str(file), '--hosts', str(hosts), *args]
        if process:
            result = subprocess.run([sys.executable, *argv], text=True, capture_output=True, timeout=30)
            code, out, err = result.returncode, result.stdout, result.stderr
        else:
            out_buffer, err_buffer = io.StringIO(), io.StringIO()
            with patch.object(sys, 'argv', argv), patch.object(sys, 'path', [str(REG.parent), *sys.path]), redirect_stdout(out_buffer), redirect_stderr(err_buffer):
                code = module.main()
            out, err = out_buffer.getvalue(), err_buffer.getvalue()
        assert (code == 0) == ok, err
        return out if ok else err
    return file, call


def test_shared_host_multiple_tenants_and_temporal_transfer(registry):
    file, call = registry
    call('tenant-add', 'cust-a', 'Customer A')
    call('tenant-add', 'cust-b', 'Customer B')
    call('vm-add', V1, H1, 'same-name', '--tenant', 'cust-a', '--at', '100')
    call('vm-add', V2, H1, 'same-name', '--tenant', 'cust-b', '--at', '100')
    call('vm-move', V1, H2, '--at', '200')
    call('vm-assign', V1, 'cust-b', '--at', '300')
    call('vm-rename', V1, 'new-name')
    call('vm-unassign', V1, '--at', '400')
    call('vm-retire', V1, '--at', '500')
    for at, host, tenant in [(100, H1, 'cust-a'), (199, H1, 'cust-a'), (200, H2, 'cust-a'),
                             (300, H2, 'cust-b'), (400, H2, 'internal')]:
        entry = json.loads(call('vm-at', V1, '--at', str(at)))
        assert (entry['host_id'], entry['tenant_id']) == (host, tenant)
    assert json.loads(call('vm-at', V1, '--at', '99')) is None
    assert json.loads(call('vm-at', V1, '--at', '500')) is None
    assert json.loads(call('vm-at', V2, '--at', '500'))['tenant_id'] == 'cust-b'
    assert json.loads(call('list'))['vms'][V1]['name'] == 'new-name'
    assert file.stat().st_mode & 0o777 == 0o600
    call('vm-add', V1, H1, 'reuse', ok=False)


def test_invalid_refs_time_and_disabled_tenant_leave_file_unchanged(registry):
    file, call = registry
    call('tenant-add', 'cust-a', 'A')
    call('vm-add', V1, H1, 'vm', '--at', '100')
    before = file.read_bytes()
    for args in [('tenant-add', 'internal', 'bad'), ('tenant-add', 'Bad!', 'bad'),
                 ('vm-add', V2, V1, 'unknown-host'), ('vm-assign', V1, 'unknown'),
                 ('vm-assign', V1, 'cust-a', '--at', '99'), ('vm-move', V1, H2, '--at', '99999999999')]:
        call(*args, ok=False)
        assert file.read_bytes() == before
    call('tenant-disable', 'cust-a')
    call('vm-assign', V1, 'cust-a', '--at', '200', ok=False)
    call('tenant-enable', 'cust-a')
    call('vm-assign', V1, 'cust-a', '--at', '200')
    call('tenant-disable', 'cust-a', ok=False)
    call('vm-unassign', V1, '--at', '300')
    call('tenant-disable', 'cust-a')
    assert json.loads(call('vm-at', V1, '--at', '200'))['tenant_id'] == 'cust-a'


def test_serializes_concurrent_writers(registry):
    file, call = registry
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda i: call('tenant-add', f'cust-{i}', f'Customer {i}', process=True), range(15)))
    assert len(json.loads(call('list'))['tenants']) == 15


def test_invalid_file_never_overwritten(registry):
    file, call = registry
    call('tenant-add', 'cust-a', 'A')
    for text in ['{"version":2,"tenants":{},"vms":{}}', '{"version":1,"version":1,"tenants":{},"vms":{}}', '{']:
        file.write_text(text)
        call('tenant-add', 'cust-b', 'B', ok=False)
        assert file.read_text() == text
    file.unlink()
    victim = file.parent / 'victim'
    victim.write_text('untouched')
    file.symlink_to(victim)
    call('tenant-add', 'cust-b', 'B', ok=False)
    assert victim.read_text() == 'untouched'


@pytest.mark.skipif(os.geteuid()!=0, reason='operator CLI requires root')
def test_wrapper_preserves_legacy_inventory_and_customer_gate(tmp_path):
    cfg = tmp_path / 'central'
    script = ROOT / 'tools/setup-central-monitoring.sh'
    env = {**os.environ, 'KVM_VM_CENTRAL_DIR': str(cfg)}
    def run(*args):
        return subprocess.run(['bash', str(script), *args], env=env, capture_output=True, text=True, timeout=15)
    assert run('render', str(cfg)).returncode == 0
    assert run('inventory', 'add', H1, 'host1', 'dc1').returncode == 0
    old = (cfg/'inventory/hosts.json').read_bytes()
    assert run('registry', 'tenant-add', 'customer-a', 'A').returncode == 0
    assert run('registry', 'vm-add', V1, H1, 'vm', '--tenant', 'customer-a').returncode == 0
    assert run('render', str(cfg)).returncode == 0
    assert (cfg/'inventory/hosts.json').read_bytes() == old
    assert json.loads((cfg/'registry/ownership.json').read_text())['vms'][V1]['history'][0]['tenant_id'] == 'customer-a'
    result = run('customer-readiness')
    assert result.returncode == 2 and 'BLOCKED' in result.stdout
