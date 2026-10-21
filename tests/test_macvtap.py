"""Command-boundary Macvtap tests; only host effects are simulated."""
import json
import subprocess

import pytest

from test_unit import kvm_vm, reinstall_machine


@pytest.fixture
def network_machine(reinstall_machine, monkeypatch):
    machine = reinstall_machine
    machine.update(failure=None, install_succeeds=True, commands=[])
    sysnet = machine["root"] / "sysnet"
    (sysnet / "eno1" / "device").mkdir(parents=True)
    monkeypatch.setattr(kvm_vm, "NET_SYS_DIR", sysnet, raising=False)
    machine["link"] = {"ifname": "eno1", "link_type": "ether", "flags": ["UP"]}
    external = kvm_vm.subprocess.run

    def host(cmd, **kwargs):
        machine["commands"].append(cmd)
        if cmd[0] == "ip":
            return subprocess.CompletedProcess(cmd, machine.get("ip_code", 0),
                machine.get("ip_output", json.dumps([machine["link"]])), "")
        return external(cmd, **kwargs)

    monkeypatch.setattr(kvm_vm.subprocess, "run", host)
    return machine


def invoke(*argv):
    args = kvm_vm.build_parser().parse_args(argv)
    return args.func(args)


def test_create_macvtap_persists_direct_attachment(network_machine):
    machine = network_machine
    definition = machine["args"].definition
    raw = kvm_vm.yaml_load(kvm_vm.Path(definition))
    raw["network"] = {"mode": "macvtap", "parent": "eno1", "mac": "52:54:00:11:22:33"}
    kvm_vm.yaml_dump(kvm_vm.Path(definition), raw)
    assert invoke("create", definition, "--no-start") == 0
    saved = kvm_vm.yaml_load(kvm_vm.DEF_DIR / "clone01.yaml")
    assert saved["network"]["mode"] == "macvtap"
    assert saved["network"]["parent"] == "eno1"
    install = next(cmd for cmd in machine["commands"] if cmd[0] == "virt-install")
    assert install[install.index("--network") + 1] == (
        "type=direct,source=eno1,source.mode=bridge,model=virtio,mac=52:54:00:11:22:33")
    assert install.count("--network") == 1
    data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    assert data["users"][0]["lock_passwd"] is True


@pytest.mark.parametrize("operation", ["create", "clone"])
def test_guest_agent_channel_is_explicit_for_generic_variant(network_machine, operation):
    definition = kvm_vm.Path(network_machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["vm"]["os_variant"] = "generic"
    raw["cloud_init"]["qemu_guest_agent"] = True
    kvm_vm.yaml_dump(definition, raw)
    args = [operation] + (["test01"] if operation == "clone" else [])
    assert invoke(*args, str(definition), "--no-start") == 0
    install = next(cmd for cmd in network_machine["commands"] if cmd[0] == "virt-install")
    assert "--channel" in install
    assert install[install.index("--channel") + 1] == "unix,target.type=virtio,target.name=org.qemu.guest_agent.0"


@pytest.mark.parametrize("original", ["bridge", "nat", "macvtap"])
def test_validate_macvtap_override(network_machine, monkeypatch, capsys, original):
    definition = kvm_vm.Path(network_machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["network"]["mode"] = original
    raw["network"]["parent"] = "eno9"
    kvm_vm.yaml_dump(definition, raw)
    monkeypatch.setenv("KVM_VM_MACVTAP", "eno1")
    monkeypatch.setenv("KVM_VM_IPV4", "192.0.2.10/24")
    monkeypatch.setenv("KVM_VM_GATEWAY", "192.0.2.1")
    monkeypatch.setenv("KVM_VM_DNS", "192.0.2.53")
    assert invoke("validate", str(definition)) == 0
    net = kvm_vm.yaml.safe_load(capsys.readouterr().out)["network"]
    assert net["mode"] == "macvtap" and net["parent"] == "eno1"
    assert net["ipv4"] == {"method": "static", "address": "192.0.2.10/24",
                           "gateway": "192.0.2.1", "dns": ["192.0.2.53"]}


@pytest.mark.parametrize("parent,bridge,mode", [
    ("", None, "nat"), ("eno1", "br0", "nat"), (None, "br0", "macvtap"),
    ("eno1,source.mode=private", None, "nat"),
])
def test_macvtap_conflicts_fail_before_mutation(network_machine, monkeypatch, parent, bridge, mode):
    machine = network_machine
    definition = kvm_vm.Path(machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["network"].update(mode=mode, parent="eno1")
    kvm_vm.yaml_dump(definition, raw)
    if parent is not None:
        monkeypatch.setenv("KVM_VM_MACVTAP", parent)
    if bridge is not None:
        monkeypatch.setenv("KVM_VM_BRIDGE", bridge)
    with pytest.raises(kvm_vm.KVMError, match="conflict|network.parent"):
        invoke("create", str(definition))
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    assert not machine["commands"]


@pytest.mark.parametrize("problem", ["missing", "json", "bridge", "vlan", "bond", "wifi", "virtual", "down", "enslaved"])
def test_create_refuses_unsupported_parent(network_machine, monkeypatch, problem):
    machine = network_machine
    monkeypatch.setenv("KVM_VM_MACVTAP", "eno1")
    if problem == "missing":
        machine["ip_code"] = 1
    elif problem == "json":
        machine["ip_output"] = "invalid"
    elif problem in ("bridge", "vlan", "bond"):
        machine["link"]["linkinfo"] = {"info_kind": problem}
    elif problem == "wifi":
        (kvm_vm.NET_SYS_DIR / "eno1" / "wireless").mkdir()
    elif problem == "virtual":
        (kvm_vm.NET_SYS_DIR / "eno1" / "device").rmdir()
    elif problem == "down":
        machine["link"]["flags"] = []
    else:
        machine["link"]["master"] = "br0"
    with pytest.raises(kvm_vm.KVMError, match="Macvtap parent"):
        invoke("create", machine["args"].definition)
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    assert not any(cmd[0] in ("qemu-img", "virt-install") for cmd in machine["commands"])
    assert all(cmd[:4] == ["ip", "-j", "-d", "link"] for cmd in machine["commands"] if cmd[0] == "ip")


@pytest.mark.parametrize("failure", [False, True])
def test_reinstall_preserves_macvtap_despite_environment(network_machine, monkeypatch, failure):
    machine = network_machine
    definition = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(definition)
    raw["network"] = {"mode": "macvtap", "parent": "eno1", "mac": "52:54:00:11:22:33"}
    kvm_vm.yaml_dump(definition, raw)
    monkeypatch.setenv("KVM_VM_MACVTAP", "eno9")
    monkeypatch.setenv("KVM_VM_BRIDGE", "wrong-br")
    if failure:
        machine["failure"] = "refused"
        original = machine["disk"].read_bytes()
        with pytest.raises(kvm_vm.KVMError, match="undefine"):
            invoke("reinstall", "test01", "--yes", "--no-start")
        assert machine["disk"].read_bytes() == original
    else:
        assert invoke("reinstall", "test01", "--yes", "--no-start") == 0
        assert kvm_vm.yaml_load(definition)["network"]["parent"] == "eno1"
    assert kvm_vm.os.environ["KVM_VM_MACVTAP"] == "eno9"
    assert kvm_vm.os.environ["KVM_VM_BRIDGE"] == "wrong-br"


def test_clone_and_delete_macvtap_without_changing_parent(network_machine, monkeypatch):
    machine = network_machine
    monkeypatch.setenv("KVM_VM_MACVTAP", "eno1")
    assert invoke("clone", "test01", machine["args"].definition, "--no-start") == 0
    assert kvm_vm.yaml_load(kvm_vm.DEF_DIR / "clone01.yaml")["network"]["parent"] == "eno1"
    assert machine["disk"].read_bytes() == b"original guest data"
    assert invoke("delete", "test01", "--yes") == 0
    assert (kvm_vm.NET_SYS_DIR / "eno1" / "device").is_dir()
    assert all(cmd[4] == "show" for cmd in machine["commands"] if cmd[0] == "ip")


def test_macvtap_dry_run_checks_parent_without_creating_guest(network_machine, monkeypatch):
    monkeypatch.setenv("KVM_VM_MACVTAP", "eno1")
    assert invoke("create", network_machine["args"].definition, "--dry-run") == 0
    assert any(cmd[0] == "ip" for cmd in network_machine["commands"])
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    assert not (kvm_vm.STATE_DIR / "clone01.json").exists()
