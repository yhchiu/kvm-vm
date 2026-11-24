"""Domain identity consistency and additive metric labels."""
import subprocess
import pytest
from test_unit import kvm_vm
from test_monitor import _stats

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


def test_parse_uuid_name():
    assert kvm_vm.parse_domain_ids(f'{A} web01\n{B} vm with space\n\n') == {'web01': A, 'vm with space': B}


@pytest.mark.parametrize('text', ['not-uuid web01', A, f'{A} vm\n{B} vm', f'{A} vm\n{A} other'])
def test_invalid_list(text):
    with pytest.raises(kvm_vm.KVMError):
        kvm_vm.parse_domain_ids(text)


def test_recreated_name_during_collection_is_skipped(monkeypatch):
    responses = iter([f'{A} web01\n{B} keep\n', "Domain: 'web01'\nstate.state=1\nDomain: 'keep'\nstate.state=5\n", f'{B} keep\n33333333-3333-4333-8333-333333333333 web01\n'])
    monkeypatch.setattr(kvm_vm, 'run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, next(responses), ''))
    assert kvm_vm.read_domstats() == {'keep': {'state.state': '5', 'vm_id': B}}


def test_same_name_new_uuid_never_uses_old_rates():
    old = {**_stats(), 'vm_id': A}
    new = {**_stats(cpu_time=9_000_000_000, disk_read=400), 'vm_id': B}
    vm = kvm_vm.monitor_resources('web01', new, old, 1, 1001)
    assert vm['vm_id'] == B
    assert vm['cpu_percent'] is None and vm['disk_read_bytes_per_sec'] is None
    text = kvm_vm.prometheus_text({'web01': new}, {'web01': old}, 1, 1001,
                                  managed=False, success=True, duration=0, last_success=1001)
    assert f'kvm_vm_domain_disk_read_bytes_total{{disk="vda",vm="web01",vm_id="{B}"}} 400' in text
    assert f'kvm_vm_domain_cpu_used_cores{{vm="web01",vm_id="{B}"}}' not in text
    for line in text.splitlines():
        if line.startswith('kvm_vm_domain_'):
            assert f'vm_id="{B}"' in line


def test_stable_id_retained_with_renamed_domain():
    stats = {**_stats(), 'vm_id': A}
    assert kvm_vm.monitor_resources('new-name', stats, None, 1, 1001)['vm_id'] == A
