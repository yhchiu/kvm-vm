"""Prometheus exporter tests without libvirt, network privileges or background daemons."""

from __future__ import annotations

import subprocess
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest

from test_monitor import _stats
from test_unit import kvm_vm


def _render(stats=None, previous=None, elapsed=5, now=1001, managed=False):
    return kvm_vm.prometheus_text(stats if stats is not None else {"web01": _stats()},
                                  previous, elapsed, now, managed=managed,
                                  success=True, duration=0.02, last_success=now)


def test_exporter_prometheus_format_help_type_units_and_raw_counters():
    older = {"web01": _stats()}
    newer = {"web01": _stats(cpu_time=6_000_000_000, disk_read=1100,
                              disk_write=1200, net_rx=1300, net_tx=1400)}
    result = _render(newer, older)
    assert result.endswith("\n")
    assert "# TYPE kvm_vm_domain_cpu_time_seconds_total counter\n" in result
    assert 'kvm_vm_domain_cpu_time_seconds_total{vm="web01"} 6\n' in result
    assert 'kvm_vm_domain_cpu_used_cores{vm="web01"} 1\n' in result
    assert 'kvm_vm_domain_cpu_usage_ratio{vm="web01"} 0.5\n' in result
    assert 'kvm_vm_domain_disk_read_bytes_total{disk="vda",vm="web01"} 1100\n' in result
    assert 'kvm_vm_domain_disk_write_bytes_total{disk="vda",vm="web01"} 1200\n' in result
    assert 'kvm_vm_domain_network_receive_bytes_total{interface="vnet0",vm="web01"} 1300\n' in result
    assert 'kvm_vm_domain_network_transmit_bytes_total{interface="vnet0",vm="web01"} 1400\n' in result
    assert 'kvm_vm_domain_memory_guest_used_bytes{vm="web01"} 3221225472\n' in result
    assert 'kvm_vm_domain_memory_assigned_bytes{vm="web01"} 4294967296\n' in result
    # HELP and TYPE precede all samples, and are unique per metric family.
    for name in kvm_vm.PROMETHEUS_METRICS:
        assert result.count(f"# HELP {name} ") == 1
        assert result.count(f"# TYPE {name} ") == 1
        rows = result.splitlines()
        assert rows.index(next(x for x in rows if x.startswith("# TYPE " + name + " "))) < next((i for i,x in enumerate(rows) if x.startswith(name + "{") or x.startswith(name + " ")), len(rows))


def test_missing_guest_memory_is_absent_not_zero_and_no_cpu_rate_on_first_sample():
    current = _stats()
    current.pop("balloon.usable")
    result = _render({"web01": current})
    assert 'kvm_vm_domain_memory_assigned_bytes{vm="web01"}' in result
    assert 'kvm_vm_domain_memory_guest_used_bytes{vm="web01"}' not in result
    assert 'kvm_vm_domain_cpu_time_seconds_total{vm="web01"}' in result
    assert 'kvm_vm_domain_cpu_usage_ratio{vm="web01"}' not in result


def test_offline_vm_state_no_invalid_counter_and_no_stale_guest_usage():
    result = _render({"offline": _stats(state=5)})
    assert 'kvm_vm_domain_state{state="shut off",vm="offline"} 1' in result
    assert 'kvm_vm_domain_memory_assigned_bytes{vm="offline"}' in result
    assert 'kvm_vm_domain_cpu_time_seconds_total{vm="offline"}' not in result
    assert 'kvm_vm_domain_disk_read_bytes_total{disk="vda",vm="offline"}' not in result
    assert 'kvm_vm_domain_memory_guest_used_bytes{vm="offline"}' not in result


def test_counter_reset_does_not_produce_negative_cpu_gauge():
    result = _render({"web01": _stats(cpu_time=1_000_000_000)},
                     {"web01": _stats(cpu_time=20_000_000_000)})
    assert 'kvm_vm_domain_cpu_time_seconds_total{vm="web01"} 1\n' in result
    assert 'kvm_vm_domain_cpu_used_cores{vm="web01"}' not in result


def test_managed_filter_applies_to_all_domain_series(monkeypatch):
    monkeypatch.setattr(kvm_vm, "load_state", lambda name: {"managed": True} if name == "one" else None)
    result = _render({"one": _stats(), "two": _stats()}, managed=True)
    assert 'vm="one"' in result
    assert 'vm="two"' not in result


def test_escaped_domain_and_device_labels_cannot_inject_samples():
    stats = _stats(device_name='vd"x\\q\nmalicious_metric 1', iface='vnet\n0')
    result = _render({'name"\\\nattack': stats})
    assert 'vm="name\\"\\\\\\nattack"' in result
    assert 'disk="vd\\"x\\\\q\\nmalicious_metric 1"' in result
    assert '\nmalicious_metric 1\n' not in result
    assert '\nattack' not in result.replace('\\nattack', '')


def test_collector_handles_failed_virsh_and_recovery_without_stale_series(monkeypatch):
    values = iter([{"web01": _stats()}, RuntimeError("libvirt unavailable"),
                   {"web01": _stats(cpu_time=7_000_000_000)},
                   {"web01": _stats(cpu_time=9_000_000_000)}])
    def fake_domstats(name, *, timeout):
        assert timeout == 3
        val = next(values)
        if isinstance(val, Exception):
            raise val
        return val
    monkeypatch.setattr(kvm_vm, "read_domstats", fake_domstats)
    collector = kvm_vm.PrometheusVMCollector(None, False, 3)
    assert collector.collect()
    assert b'vm="web01"' in collector.snapshot()[1]
    assert not collector.collect()
    success, body = collector.snapshot()
    assert not success
    assert b'kvm_vm_collector_success 0\n' in body
    assert b'vm="web01"' not in body
    assert collector.collect()
    assert b'kvm_vm_domain_cpu_used_cores{vm="web01"}' not in collector.snapshot()[1]
    assert collector.collect()
    assert b'kvm_vm_domain_cpu_used_cores{vm="web01"}' in collector.snapshot()[1]


def test_http_metrics_health_and_not_found(monkeypatch):
    monkeypatch.setattr(kvm_vm, "read_domstats", lambda *a, **kw: {"web01": _stats()})
    collector = kvm_vm.PrometheusVMCollector(None, False, 10)
    server = HTTPServer(("127.0.0.1", 0), kvm_vm.make_prometheus_handler(collector))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/metrics", timeout=3) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith("text/plain; version=0.0.4")
            assert response.headers["Cache-Control"] == "no-store"
            assert b"kvm_vm_collector_success 0" in response.read()
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(base + "/healthz", timeout=3)
        assert failure.value.code == 503
        assert collector.collect()
        with urllib.request.urlopen(base + "/metrics", timeout=3) as response:
            assert b'kvm_vm_domain_state{state="running",vm="web01"} 1' in response.read()
        with urllib.request.urlopen(base + "/healthz", timeout=3) as response:
            assert response.read() == b"ok\n"
        with pytest.raises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(base + "/foo", timeout=3)
        assert failure.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("opts, message", [
    (["--interval", "nan"], "--interval"),
    (["--interval", "0"], "--interval"),
    (["--timeout", "inf"], "--timeout"),
    (["--timeout", "0"], "--timeout"),
    (["--listen", "localhost"], "--listen"),
    (["--listen", "::1"], "--listen"),
    (["--port", "65536"], "--port"),
    (["invalid;vm"], "Invalid domain"),
])
def test_exporter_validates_arguments_before_start(monkeypatch, opts, message):
    monkeypatch.setattr(kvm_vm, "require_commands", lambda _: None)
    args = kvm_vm.build_parser().parse_args(["exporter", *opts])
    with pytest.raises(kvm_vm.KVMError, match=message):
        args.func(args)


def test_read_domstats_applies_timeout(monkeypatch):
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(kwargs)
        output = "11111111-1111-4111-8111-111111111111 web01\n" if cmd[3] == "list" else "Domain: 'web01'\nstate.state=1\n"
        return subprocess.CompletedProcess(cmd, 0, output, "")
    monkeypatch.setattr(kvm_vm, "run", fake_run)
    assert kvm_vm.read_domstats(timeout=4)["web01"]["state.state"] == "1"
    assert len(calls) == 3
    assert all(0 < c["timeout"] <= 4 for c in calls)


def test_exporter_subprocess_serves_actual_cli_endpoint(tmp_path):
    """End-to-end CLI smoke with a fake virsh binary, no real KVM host needed."""
    import os
    import socket
    import sys
    import time
    from pathlib import Path

    virsh = tmp_path / "virsh"
    virsh.write_text("""#!/bin/sh
if [ "$3" = list ]; then
    echo "11111111-1111-4111-8111-111111111111 vm-e2e"
    exit 0
fi
cat <<'STAT'
Domain: 'vm-e2e'
state.state=1
vcpu.current=2
cpu.time=9000000000
balloon.current=1048576
block.count=1
block.0.name=vda
block.0.rd.bytes=345
net.count=1
net.0.name=vnet0
net.0.rx.bytes=567
STAT
""")
    virsh.chmod(0o755)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cmd = [sys.executable, str(Path(kvm_vm.__file__)), "exporter", "--port", str(port),
           "--interval", "0.5", "--timeout", "2"]
    child = subprocess.Popen(cmd, env={**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(40):
            if child.poll() is not None:
                raise AssertionError(f"Exporter exited early: {child.stderr.read()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=.3) as response:
                    body = response.read().decode("utf-8")
                    assert 'kvm_vm_domain_state{state="running",vm="vm-e2e",vm_id="11111111-1111-4111-8111-111111111111"} 1' in body
                    assert 'kvm_vm_domain_disk_read_bytes_total{disk="vda",vm="vm-e2e",vm_id="11111111-1111-4111-8111-111111111111"} 345' in body
                    return
            except (urllib.error.URLError, TimeoutError):
                time.sleep(.05)
        raise AssertionError("Exporter failed to start and serve metrics")
    finally:
        child.terminate()
        try:
            child.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=3)
