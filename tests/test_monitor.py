"""Read-only VM monitoring from sampled libvirt domstats (no KVM host required)."""

import json
import subprocess

import pytest

from test_unit import kvm_vm

GIB = 1024 ** 3
MIB = 1024 ** 2


def _stats(cpu_time=1_000_000_000, disk_read=100, disk_write=200,
           net_rx=300, net_tx=400, state=1, device_name="vda", iface="vnet0"):
    return {
        "state.state": str(state), "vcpu.current": "2", "cpu.time": str(cpu_time),
        "balloon.current": str(4 * GIB // 1024),
        "balloon.rss": str(2 * GIB // 1024),
        "balloon.available": str(4 * GIB // 1024),
        "balloon.usable": str(GIB // 1024),
        "balloon.last-update": "1000",
        "block.count": "1", "block.0.name": device_name,
        "block.0.capacity": str(20 * GIB), "block.0.physical": str(7 * GIB),
        "block.0.rd.bytes": str(disk_read), "block.0.wr.bytes": str(disk_write),
        "net.count": "1", "net.0.name": iface,
        "net.0.rx.bytes": str(net_rx), "net.0.tx.bytes": str(net_tx),
    }


def test_parse_domstats_multiple_domains_and_strings():
    response = """Domain: 'vm-a'
      state.state=1
      block.0.path=/some/thing=else

    Domain: vm-b
      state.state=5
      cpu.time=123
    """
    parsed = kvm_vm.parse_domstats(response)
    assert parsed == {"vm-a": {"state.state": "1", "block.0.path": "/some/thing=else"},
                      "vm-b": {"state.state": "5", "cpu.time": "123"}}


def test_parse_domstats_invalid_empty_name():
    with pytest.raises(kvm_vm.KVMError, match="empty domain"):
        kvm_vm.parse_domstats("Domain: ''\nstate.state=1")


def test_monitor_computes_correct_cpu_memory_io_and_network():
    earlier = _stats()
    later = _stats(cpu_time=2_000_000_000, disk_read=100 + 2 * MIB,
                   disk_write=200 + 3 * MIB, net_rx=300 + MIB, net_tx=400 + 4 * MIB)
    result = kvm_vm.monitor_resources("vm-a", later, earlier, 1.0, 1001.0)
    assert result["cpu_host_percent"] == 100
    assert result["cpu_percent"] == 50
    assert result["memory_guest_used_bytes"] == 3 * GIB
    assert result["memory_assigned_bytes"] == 4 * GIB
    assert result["memory_rss_bytes"] == 2 * GIB
    assert result["disk_physical_bytes"] == 7 * GIB
    assert result["disk_capacity_bytes"] == 20 * GIB
    assert result["disk_read_bytes_per_sec"] == 2 * MIB
    assert result["disk_write_bytes_per_sec"] == 3 * MIB
    assert result["net_rx_bytes_per_sec"] == MIB
    assert result["net_tx_bytes_per_sec"] == 4 * MIB


def test_monitor_does_not_claim_guest_usage_with_stale_or_missing_stats():
    stale = kvm_vm.monitor_resources("a", _stats(), _stats(), 1, 1201)
    assert stale["memory_guest_used_bytes"] is None
    missing = _stats()
    del missing["balloon.usable"]
    result = kvm_vm.monitor_resources("a", missing, missing, 1, 1001)
    assert result["memory_guest_used_bytes"] is None
    assert result["memory_assigned_bytes"] == 4 * GIB
    assert result["memory_rss_bytes"] == 2 * GIB


def test_monitor_no_rates_for_unknown_state_or_offline_vm():
    offline = _stats(state=5)
    output = kvm_vm.monitor_resources("a", offline, _stats(), 1, 1001)
    assert output["state"] == "shut off"
    assert output["cpu_percent"] is None
    assert output["memory_guest_used_bytes"] is None
    assert output["disk_read_bytes_per_sec"] is None
    assert output["net_rx_bytes_per_sec"] is None


def test_counter_reset_suppresses_spurious_rates():
    earlier = _stats(cpu_time=20_000_000_000, disk_read=5000, net_rx=5000)
    later = _stats(cpu_time=1_000_000_000, disk_read=10000, net_rx=10000)
    result = kvm_vm.monitor_resources("a", later, earlier, 1.0, 1001)
    assert result["cpu_percent"] is None
    assert result["disk_read_bytes_per_sec"] is None
    assert result["net_rx_bytes_per_sec"] is None
    assert kvm_vm.counter_rate(100, 5, 1) is None
    assert kvm_vm.counter_rate(100, 100, 0) is None


def test_missing_stats_remain_null_and_new_devices_not_compared():
    older = _stats(device_name="vda", iface="vnet0")
    newer = _stats(device_name="vdb", iface="vnet1")
    newer.pop("cpu.time")
    result = kvm_vm.monitor_resources("a", newer, older, 1.0, 1001)
    assert result["cpu_percent"] is None
    assert result["disk_read_bytes_per_sec"] is None
    assert result["net_rx_bytes_per_sec"] is None
    assert "vdb" in result["disks"]


def test_read_domstats_uses_only_read_only_virsh_options(monkeypatch):
    commands = []

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        output = "11111111-1111-4111-8111-111111111111 vm-a\n" if cmd[3] == "list" else "Domain: 'vm-a'\n state.state=1\n"
        return subprocess.CompletedProcess(cmd, 0, output, "")

    monkeypatch.setattr(kvm_vm, "run", fake_run)
    assert kvm_vm.read_domstats("vm-a") == {"vm-a": {"state.state": "1", "vm_id": "11111111-1111-4111-8111-111111111111"}}
    assert commands[1][:4] == ["virsh", "-c", kvm_vm.LIBVIRT_URI, "domstats"]
    assert "--raw" in commands[1]
    assert commands[1][-1] == "vm-a"
    assert all(opt not in commands[1] for opt in ("--enforce", "--backing"))


def test_read_domstats_failure_and_missing_single_vm(monkeypatch):
    monkeypatch.setattr(kvm_vm, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "denied"))
    with pytest.raises(kvm_vm.KVMError, match="denied"):
        kvm_vm.read_domstats("a")
    monkeypatch.setattr(kvm_vm, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    with pytest.raises(kvm_vm.KVMError, match="No statistics"):
        kvm_vm.read_domstats("a")


def test_cmd_monitor_json_managed_filter(monkeypatch, capsys):
    earlier = {"vm-a": _stats(), "vm-b": _stats()}
    later = {"vm-a": _stats(cpu_time=2_000_000_000), "vm-b": _stats(cpu_time=3_000_000_000)}
    fake = iter((earlier, later))
    monkeypatch.setattr(kvm_vm, "read_domstats", lambda *args: next(fake))
    monkeypatch.setattr(kvm_vm, "require_commands", lambda *_: None)
    monkeypatch.setattr(kvm_vm, "load_state", lambda name: {"managed": 1} if name == "vm-a" else None)
    monkeypatch.setattr(kvm_vm.time, "monotonic", iter((0.0, 0.0, 1.0)).__next__)
    monkeypatch.setattr(kvm_vm.time, "sleep", lambda _: None)
    monkeypatch.setattr(kvm_vm.time, "time", lambda: 1001.0)
    args = kvm_vm.build_parser().parse_args(["monitor", "--managed", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["interval_seconds"] == 1.0
    assert len(out["domains"]) == 1
    assert out["domains"][0]["name"] == "vm-a"
    assert out["domains"][0]["cpu_percent"] == 50


@pytest.mark.parametrize("argv, match", [
    (["monitor", "--count", "-1"], "--count"),
    (["monitor", "--interval", "0"], "--interval"),
    (["monitor", "--interval", "nan"], "--interval"),
    (["monitor", "bad;command"], "Invalid domain"),
])
def test_cmd_monitor_validates_options(monkeypatch, argv, match):
    monkeypatch.setattr(kvm_vm, "require_commands", lambda _: None)
    args = kvm_vm.build_parser().parse_args(argv)
    with pytest.raises(kvm_vm.KVMError, match=match):
        args.func(args)


def test_monitor_table_shows_total_logical_size_and_physical_size(capsys):
    stats = _stats()
    stats.update({'block.count': '2', 'block.1.name': 'vdb',
                  'block.1.capacity': str(100 * GIB), 'block.1.physical': str(3 * GIB)})
    vm = kvm_vm.monitor_resources('vm-a', stats, None, 1, 1001)
    kvm_vm.print_monitor_table({'timestamp': 'now', 'interval_seconds': 1, 'domains': [vm]})
    text = capsys.readouterr().out
    assert 'DISK SIZE' in text and 'DISK PHYS' in text
    assert '120.0GiB' in text and '10.0GiB' in text


def test_monitor_table_unknown_capacity_and_empty_domains(capsys):
    stats = _stats(state=5)
    stats.pop('block.0.capacity')
    vm = kvm_vm.monitor_resources('vm-a', stats, None, 1, 1001)
    snapshot = {'timestamp': 'now', 'interval_seconds': 1, 'domains': [vm]}
    kvm_vm.print_monitor_table(snapshot)
    lines = capsys.readouterr().out.splitlines()
    i = lines[-2].index('DISK SIZE')
    assert lines[-1][i:i+9].strip() == '-'
    snapshot['domains'] = []
    kvm_vm.print_monitor_table(snapshot)
    assert '(no domains)' in capsys.readouterr().out
