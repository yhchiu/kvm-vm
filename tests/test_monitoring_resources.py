"""Additional resource counters, base units and missing-value semantics."""
from test_unit import kvm_vm
from test_monitor import _stats


def render(stats, now=1001):
    return kvm_vm.prometheus_text({'vm': stats}, None, 0, now, managed=False,
                                  success=True, duration=0, last_success=now)


def test_extended_disk_network_counters():
    stats = {**_stats(), 'block.0.rd.reqs': '42', 'block.0.wr.reqs': '18',
             'block.0.rd.times': '1500000000', 'block.0.wr.times': '4000000',
             'block.0.fl.reqs': '3', 'block.0.fl.times': '500000000',
             'net.0.rx.pkts': '23', 'net.0.tx.pkts': '12', 'net.0.rx.errs': '0',
             'net.0.tx.errs': '2', 'net.0.rx.drop': '7', 'net.0.tx.drop': '4'}
    text = render(stats)
    disk = '{disk="vda",vm="vm"}'
    nic = '{interface="vnet0",vm="vm"}'
    for suffix, value in [('read_operations_total', '42'), ('write_operations_total', '18'), ('flush_operations_total', '3'), ('read_time_seconds_total', '1.5'), ('write_time_seconds_total', '0.004'), ('flush_time_seconds_total', '0.5')]:
        assert f'kvm_vm_domain_disk_{suffix}{disk} {value}\n' in text
    for suffix, value in [('receive_packets_total', '23'), ('transmit_packets_total', '12'), ('receive_errors_total', '0'), ('transmit_errors_total', '2'), ('receive_dropped_packets_total', '7'), ('transmit_dropped_packets_total', '4')]:
        assert f'kvm_vm_domain_network_{suffix}{nic} {value}\n' in text


def test_unknown_negative_and_offline_values():
    stats = {**_stats(), 'block.0.rd.reqs': '-1', 'net.0.rx.pkts': 'invalid', 'block.0.wr.times': '0'}
    text = render(stats)
    assert 'kvm_vm_domain_disk_read_operations_total{' not in text
    assert 'kvm_vm_domain_network_receive_packets_total{' not in text
    assert 'kvm_vm_domain_disk_write_time_seconds_total{disk="vda",vm="vm"} 0\n' in text
    assert 'kvm_vm_domain_disk_write_time_seconds_total{' not in render({**stats, 'state.state': '5'})


def test_guest_freshness_and_operation_rates():
    before = {**_stats(), 'block.0.rd.reqs': '20', 'block.0.wr.reqs': '10'}
    after = {**_stats(cpu_time=2_000_000_000), 'block.0.rd.reqs': '30', 'block.0.wr.reqs': '18'}
    vm = kvm_vm.monitor_resources('vm', after, before, 2, 1001)
    assert vm['disk_read_operations_per_sec'] == 5
    assert vm['disk_write_operations_per_sec'] == 4
    assert vm['memory_guest_stats_fresh'] is True
    assert 'kvm_vm_domain_memory_guest_stats_fresh{vm="vm"} 1\n' in render(after)
    assert 'kvm_vm_domain_memory_guest_stats_fresh{vm="vm"} 0\n' in render(after, 1201)
    assert 'kvm_vm_domain_memory_guest_used_bytes{' not in render(after, 1201)
    assert 'kvm_vm_domain_memory_guest_stats_timestamp_seconds{vm="vm"} 1000\n' in render(after)
    assert 'kvm_vm_domain_memory_guest_stats_timestamp_seconds{' not in render({**after, 'balloon.last-update': '0'})
