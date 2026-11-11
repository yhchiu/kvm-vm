"""Offline tests for tools/kvm-vm-bridge.

These tests do not create a bridge or change a real uplink. A live check is
skipped unless KVM_VM_BRIDGE_LIVE=1 on a disposable host.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from importlib.machinery import SourceFileLoader

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "kvm-vm-bridge"
_loader = SourceFileLoader("kvm_vm_bridge", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("kvm_vm_bridge", _loader)
bridge = importlib.util.module_from_spec(_spec)
sys.modules["kvm_vm_bridge"] = bridge
_spec.loader.exec_module(bridge)


def run_tool(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True,
        text=True,
        **kwargs,
    )


# --- parser ---

def test_help_and_version():
    for args in (
        ["--help"],
        ["--version"],
        ["create", "--help"],
        ["confirm", "--help"],
        ["rollback", "--help"],
        ["status", "--help"],
        ["detect", "--help"],
    ):
        result = run_tool(*args)
        assert result.returncode == 0, result.stderr
    version = run_tool("--version")
    assert "kvm-vm-bridge" in version.stdout
    assert bridge.VERSION in version.stdout


def test_create_requires_uplink():
    result = run_tool("create")
    assert result.returncode != 0
    assert "uplink" in (result.stderr + result.stdout).lower()


def test_rollback_without_yes_is_refused():
    result = run_tool("rollback", "--bridge", "br0")
    assert result.returncode == 1
    assert "without --yes" in result.stderr
    assert "must run as root" not in result.stderr


# --- backend detection ---

def _completed(cmd, code, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, code, stdout, stderr)


def test_detect_ifupdown_from_interfaces_file(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    net = etc / "network"
    net.mkdir(parents=True)
    (net / "interfaces").write_text("auto enp4s0\niface enp4s0 inet static\n    address 203.0.113.10/29\n", encoding="utf-8")
    monkeypatch.setenv("KVM_VM_BRIDGE_ETC", str(etc))

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "is-active", "--quiet"] and cmd[3] == "networking":
            return _completed(cmd, 0)
        if cmd[:3] == ["systemctl", "is-enabled", "--quiet"] and cmd[3] == "networking":
            return _completed(cmd, 0)
        return _completed(cmd, 1, stderr="inactive")

    monkeypatch.setattr(bridge, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(bridge, "run", fake_run)
    assert bridge.detect_backend("enp4s0") == "ifupdown"


def test_detect_networkmanager_from_nmcli_stub(tmp_path, monkeypatch):
    monkeypatch.setenv("KVM_VM_BRIDGE_ETC", str(tmp_path / "etc"))

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "is-active", "--quiet"] and cmd[3] == "NetworkManager":
            return _completed(cmd, 0)
        if cmd[0] == "nmcli" and "GENERAL.NM-MANAGED" in cmd:
            return _completed(cmd, 0, "GENERAL.NM-MANAGED:yes\n")
        if cmd[0] == "nmcli" and "GENERAL.STATE" in cmd:
            return _completed(cmd, 0, "GENERAL.STATE:100 (connected)\n")
        if cmd[0] == "nmcli" and "GENERAL.CONNECTION" in cmd:
            return _completed(cmd, 0, "GENERAL.CONNECTION:eno1\n")
        return _completed(cmd, 1)

    monkeypatch.setattr(bridge, "which", lambda name: f"/usr/bin/{name}" if name in {"systemctl", "nmcli"} else None)
    monkeypatch.setattr(bridge, "run", fake_run)
    assert bridge.detect_backend("eno1") == "networkmanager"
    assert bridge.nm_device_connection("eno1") == "eno1"


def test_detect_networkd_from_network_file(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    net = etc / "systemd" / "network"
    net.mkdir(parents=True)
    (net / "10-uplink.network").write_text("[Match]\nName=eno1\n[Network]\nDHCP=yes\n", encoding="utf-8")
    monkeypatch.setenv("KVM_VM_BRIDGE_ETC", str(etc))

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "is-active", "--quiet"] and cmd[3] == "systemd-networkd":
            return _completed(cmd, 0)
        return _completed(cmd, 1)

    monkeypatch.setattr(bridge, "which", lambda name: "/usr/bin/systemctl" if name == "systemctl" else None)
    monkeypatch.setattr(bridge, "run", fake_run)
    assert bridge.detect_backend("eno1") == "networkd"


def test_detect_runtime_when_no_manager_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("KVM_VM_BRIDGE_ETC", str(tmp_path / "empty-etc"))
    monkeypatch.setattr(bridge, "which", lambda name: None)
    monkeypatch.setattr(bridge, "systemd_is_active", lambda unit: False)
    monkeypatch.setattr(bridge, "service_enabled_or_active", lambda unit: False)
    assert bridge.detect_backend("eth0") == "runtime"


# --- rollback and archive scripts ---

def _sample_meta():
    return {
        "bridge": "br0",
        "uplink": "enp4s0",
        "uplink_mac": "00:30:48:d5:41:72",
        "ipv4": [{"family": "inet", "local": "203.0.113.10", "prefixlen": 29, "scope": "global"}],
        "ipv6": [{"family": "inet6", "local": "2001:db8::10", "prefixlen": 64, "scope": "global"}],
        "gateway": "203.0.113.9",
        "default_routes": [{"dst": "default", "gateway": "203.0.113.9", "dev": "enp4s0", "flags": ["onlink"]}],
    }


def test_rollback_script_restores_uplink_and_deletes_only_the_bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "LOG_FILE", tmp_path / "bridge.log")
    sdir = tmp_path / "sessions" / "br0"
    sdir.mkdir(parents=True)
    (sdir / "ip-route.json").write_text("[]", encoding="utf-8")
    script = bridge.write_rollback_script(sdir, _sample_meta())
    text = script.read_text(encoding="utf-8")
    assert "203.0.113.10/29" in text
    assert "2001:db8::10/64" in text
    assert "203.0.113.9" in text
    assert "onlink" in text
    assert 'ip link delete dev "$BRIDGE"' in text
    assert "ip link delete dev enp4s0" not in text
    assert "ip link delete dev \"$UPLINK\"" not in text
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_archive_script_moves_a_rolled_back_session(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(bridge, "STATE_DIR", state)
    monkeypatch.setattr(bridge, "LOG_FILE", tmp_path / "bridge.log")
    sdir = state / "sessions" / "br0"
    sdir.mkdir(parents=True)
    meta = {"bridge": "br0", "status": "pending-confirm", "confirmed": False}
    (sdir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    script = bridge.write_archive_script(sdir)
    subprocess.run(["bash", "-n", str(script)], check=True)
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not sdir.exists()
    archived = list((state / "archived").iterdir())
    assert len(archived) == 1
    assert archived[0].name.startswith("br0-")
    saved = json.loads((archived[0] / "meta.json").read_text(encoding="utf-8"))
    assert saved["status"] == "rolled-back"
    assert saved["rolled_back_at"]
    assert "archived session" in (tmp_path / "bridge.log").read_text(encoding="utf-8")


def test_watchdog_script_archives_after_rollback_and_uses_valid_systemd_flags(tmp_path, monkeypatch):
    state = tmp_path / "state"
    sdir = state / "sessions" / "br0"
    sdir.mkdir(parents=True)
    (sdir / "rollback.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "STATE_DIR", state)
    monkeypatch.setattr(bridge, "LOG_FILE", tmp_path / "bridge.log")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "systemd-run":
            return _completed(cmd, 0, "Running as unit")
        return _completed(cmd, 0)

    monkeypatch.setattr(bridge, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bridge, "run", fake_run)
    info = bridge.arm_watchdog("br0", 45, sdir)
    assert info["method"] == "systemd-run"
    systemd = next(cmd for cmd in calls if cmd[0] == "systemd-run")
    assert "--no-block" in systemd
    assert not any(arg.startswith("--remain-after-exit") for arg in systemd)
    text = (sdir / "watchdog.sh").read_text(encoding="utf-8")
    assert "rollback.sh" in text
    assert "archive.sh" in text
    rollback_at = text.index("rollback.sh")
    archive_at = text.index("archive.sh")
    assert rollback_at < archive_at
    for line in text.splitlines():
        if "rollback.sh" in line:
            assert not line.strip().startswith("exec")
        if "archive.sh" in line:
            assert line.strip().startswith("exec ")
    subprocess.run(["bash", "-n", str(sdir / "watchdog.sh")], check=True)
    subprocess.run(["bash", "-n", str(sdir / "archive.sh")], check=True)


def test_nohup_fallback_uses_setsid_and_archives(tmp_path, monkeypatch):
    state = tmp_path / "state"
    sdir = state / "sessions" / "br0"
    sdir.mkdir(parents=True)
    (sdir / "rollback.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "STATE_DIR", state)
    monkeypatch.setattr(bridge, "LOG_FILE", tmp_path / "bridge.log")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[0] == "systemd-run":
            return _completed(cmd, 1, stderr="refused")
        if cmd[0] == "bash":
            return _completed(cmd, 0, "4242\n")
        return _completed(cmd, 0)

    monkeypatch.setattr(bridge, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bridge, "run", fake_run)
    info = bridge.arm_watchdog("br0", 30, sdir)
    assert info["method"] == "nohup"
    assert info["pid"] == 4242
    bash = next(cmd for cmd in calls if cmd[0] == "bash")
    joined = " ".join(bash)
    assert "setsid -f" in joined
    assert "archive.sh" in joined
    assert "rollback.sh" in joined


# --- ifupdown persist ---

def test_ifupdown_persist_comments_uplink_and_rollback_restores_it(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    net = etc / "network"
    net.mkdir(parents=True)
    original = "auto enp4s0\niface enp4s0 inet static\n    address 203.0.113.10/29\n    gateway 203.0.113.9\n"
    (net / "interfaces").write_text(original, encoding="utf-8")
    (etc / "resolv.conf").write_text("nameserver 1.1.1.1\n", encoding="utf-8")
    monkeypatch.setenv("KVM_VM_BRIDGE_ETC", str(etc))
    state = tmp_path / "state"
    monkeypatch.setattr(bridge, "STATE_DIR", state)
    monkeypatch.setattr(bridge, "LOG_FILE", tmp_path / "bridge.log")
    sdir = state / "sessions" / "br0" / "files"
    sdir.mkdir(parents=True)
    (sdir / "interfaces").write_text(original, encoding="utf-8")

    meta = _sample_meta()
    meta.update({"backend": "ifupdown", "persist_written": True})
    written = bridge.persist_ifupdown(meta)
    conf = Path(written[0])
    assert conf.name == "kvm-vm-bridge-br0.conf"
    text = conf.read_text(encoding="utf-8")
    assert "stp_state 0" in text
    assert "forward_delay 0" in text
    assert "203.0.113.10/29" in text
    commented = (net / "interfaces").read_text(encoding="utf-8")
    assert "iface enp4s0" in commented
    assert "# iface enp4s0 inet static" in commented or "#iface enp4s0 inet static" in commented
    assert commented.strip().startswith("#") or "# kvm-vm-bridge:" in commented

    meta["persist_paths"] = written
    bridge.remove_persistent(meta)
    assert not conf.exists()
    assert (net / "interfaces").read_text(encoding="utf-8") == original


# --- preflight refusals (mocked sysfs, no live interface) ---

def _sysfs(tmp_path, monkeypatch):
    root = tmp_path / "sys"
    root.mkdir()
    monkeypatch.setenv("KVM_VM_BRIDGE_SYS_NET", str(root))
    monkeypatch.setattr(bridge, "which", lambda name: "/sbin/ip" if name == "ip" else None)
    monkeypatch.setattr(bridge, "ip_json", lambda args: [{"ifname": args[-1], "master": None}])
    return root


def _uplink(root: Path, name: str, *, state: str = "up", bridge: bool = False, master: str = "") -> None:
    iface = root / name
    iface.mkdir(exist_ok=True)
    (iface / "operstate").write_text(state + "\n", encoding="utf-8")
    (iface / "carrier").write_text("1\n" if state == "up" else "0\n", encoding="utf-8")
    (iface / "address").write_text("00:11:22:33:44:55\n", encoding="utf-8")
    if bridge:
        (iface / "bridge").mkdir()
    if master:
        master_dir = root / master
        master_dir.mkdir(exist_ok=True)
        (iface / "master").symlink_to(master_dir)


def test_preflight_refuses_missing_down_bridge_enslaved_and_existing(tmp_path, monkeypatch):
    root = _sysfs(tmp_path, monkeypatch)
    with pytest.raises(bridge.BridgeError, match="Uplink not found"):
        bridge.preflight("br0", "enp4s0")

    _uplink(root, "enp4s0", state="down")
    with pytest.raises(bridge.BridgeError, match="not up"):
        bridge.preflight("br0", "enp4s0")

    (root / "enp4s0" / "operstate").write_text("up\n", encoding="utf-8")
    (root / "enp4s0" / "carrier").write_text("1\n", encoding="utf-8")
    (root / "enp4s0" / "bridge").mkdir()
    with pytest.raises(bridge.BridgeError, match="already a bridge"):
        bridge.preflight("br0", "enp4s0")

    (root / "enp4s0" / "bridge").rmdir()
    _uplink(root, "enp4s0", master="br-existing")
    with pytest.raises(bridge.BridgeError, match="already enslaved to br-existing"):
        bridge.preflight("br0", "enp4s0")

    (root / "enp4s0" / "master").unlink()
    (root / "br0").mkdir()
    monkeypatch.setattr(bridge, "global_ipv4", lambda name: [{"local": "203.0.113.10", "prefixlen": 29}])
    with pytest.raises(bridge.BridgeError, match="already exists"):
        bridge.preflight("br0", "enp4s0")


# --- bridged guest IPv4 warning (B-005) ---

def _facts(**overrides):
    facts = {
        "br_netfilter": True,
        "call_iptables": "1",
        "readable": True,
        "format": "iptables",
        "rules": "-P FORWARD DROP\n-A FORWARD -j DOCKER-USER\n",
    }
    facts.update(overrides)
    return facts


def test_forward_warning_when_policy_drops_the_bridge():
    message = bridge.bridged_ipv4_warning("br0", _facts())
    assert message is not None
    assert "FORWARD" in message
    assert "br0" in message
    assert "policy to ACCEPT" in message


def test_no_forward_warning_when_bridge_is_accepted_both_ways():
    rules = "\n".join([
        "-P FORWARD DROP",
        "-A FORWARD -i br0 -j ACCEPT",
        "-A FORWARD -o br0 -j ACCEPT",
    ])
    assert bridge.bridged_ipv4_warning("br0", _facts(rules=rules)) is None


def test_forward_warning_when_only_one_direction_is_accepted():
    rules = "-P FORWARD DROP\n-A FORWARD -i br0 -j ACCEPT\n"
    message = bridge.bridged_ipv4_warning("br0", _facts(rules=rules))
    assert message is not None
    assert "-o br0" in message


def test_per_bridge_zero_does_not_override_global_sysctl(monkeypatch):
    def read(path):
        text = str(path)
        if text.endswith("bridge-nf-call-iptables"):
            return "1"
        if text.endswith("nf_call_iptables"):
            return "0"
        return None

    monkeypatch.setattr(bridge, "_read_sysctl", read)
    assert bridge.bridge_calls_iptables("br0") == "1"

    def both_off(path):
        text = str(path)
        if text.endswith("bridge-nf-call-iptables") or text.endswith("nf_call_iptables"):
            return "0"
        return None

    monkeypatch.setattr(bridge, "_read_sysctl", both_off)
    assert bridge.bridge_calls_iptables("br0") == "0"


def test_no_forward_warning_without_br_netfilter_or_when_policy_accepts():
    assert bridge.bridged_ipv4_warning("br0", _facts(br_netfilter=False)) is None
    assert bridge.bridged_ipv4_warning("br0", _facts(call_iptables="0")) is None
    assert bridge.bridged_ipv4_warning("br0", _facts(rules="-P FORWARD ACCEPT\n")) is None


def test_forward_warning_when_policy_cannot_be_read():
    message = bridge.bridged_ipv4_warning("br0", _facts(readable=False, rules=""))
    assert message is not None
    assert "could not be read" in message


def test_nft_forward_chain_parser():
    text = "\n".join([
        "table ip filter {",
        "  chain FORWARD {",
        "    type filter hook forward priority filter; policy drop;",
        '    iifname "br0" accept',
        '    oifname "br0" accept',
        "  }",
        "}",
    ])
    policy, accept_in, accept_out = bridge.parse_nft_forward(text, "br0")
    assert policy == "DROP"
    assert accept_in and accept_out
    assert bridge.bridged_ipv4_warning("br0", _facts(format="nft", rules=text)) is None
    dropped = text.replace('iifname "br0" accept', 'iifname "docker0" accept').replace('oifname "br0" accept', 'oifname "docker0" accept')
    message = bridge.bridged_ipv4_warning("br0", _facts(format="nft", rules=dropped))
    assert message is not None


def test_blanket_forward_accept_covers_the_bridge():
    rules = "-P FORWARD DROP\n-A FORWARD -j ACCEPT\n"
    assert bridge.bridged_ipv4_warning("br0", _facts(rules=rules)) is None


# --- live test, skipped unless explicitly requested ---

@pytest.mark.skipif(
    os.environ.get("KVM_VM_BRIDGE_LIVE") != "1",
    reason="live bridge test can change the host uplink; set KVM_VM_BRIDGE_LIVE=1 only on a disposable host",
)
def test_live_dry_run_does_not_change_the_uplink():
    """Dry-run on a real uplink. Does not confirm and does not arm a watchdog.

    Auto-rollback, confirm and explicit rollback still have to be run by hand
    on a disposable host. See docs/bridge-host.md.
    """
    uplink = os.environ.get("KVM_VM_BRIDGE_LIVE_UPLINK")
    if not uplink:
        pytest.skip("set KVM_VM_BRIDGE_LIVE_UPLINK to the disposable uplink")
    before = subprocess.run(["ip", "-br", "addr"], capture_output=True, text=True, check=True).stdout
    result = run_tool("create", "--uplink", uplink, "--bridge", "kvmvm-br-live", "--timeout", "30", "--dry-run")
    after = subprocess.run(["ip", "-br", "addr"], capture_output=True, text=True, check=True).stdout
    assert result.returncode == 0, result.stderr
    assert "dry-run: no changes made" in result.stdout
    assert before == after
