"""Real Linux/KVM refusal checks. These do not prove guest recovery or Backup Restore."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

import pytest
import yaml

from test_e2e import E2EHelper, _check_e2e_prerequisites
from process_helpers import assert_cleanup_safe, run_owned_command

_available, _reason = _check_e2e_prerequisites()
pytestmark = pytest.mark.skipif(not _available, reason=_reason)
SCRIPT = Path(__file__).resolve().parents[1] / "kvm-vm"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


@pytest.fixture
def recovery_domain():
    name = "e2e-recovery-" + uuid.uuid4().hex[:10]
    root = Path("/var/lib/libvirt/images") / name
    root.mkdir(mode=0o755)
    env = dict(os.environ, KVM_VM_BASE_DIR=str(root / "base"),
        KVM_VM_DISK_DIR=str(root / "disks"), KVM_VM_STATE_DIR=str(root / "state"),
        KVM_VM_CLOUD_DIR=str(root / "cloud"), KVM_VM_DEF_DIR=str(root / "definitions"))

    def cli(*args, check=True):
        return run_owned_command([sys.executable, str(SCRIPT), *args], env=env, check=check)

    base = root / "base.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(base), "1G"], check=True)
    definition = root / "input.yaml"
    cfg = {"version": 1, "vm": {"name": name, "memory_mib": 256, "vcpus": 1, "autostart": False},
        "storage": {"image": {"path": str(base)}, "disk_gib": 1},
        "network": {"mode": "nat", "libvirt_network": "default", "ipv4": {"method": "dhcp"}},
        "cloud_init": {"ssh_authorized_keys": ["ssh-ed25519 AAAA... recovery-test"]}}
    definition.write_text(yaml.safe_dump(cfg))
    try:
        cli("create", str(definition), "--no-start")
        state = json.loads((root / "state" / (name + ".json")).read_text())
        disk = Path(state["disk"])
        subprocess.run(["qemu-io", "-f", "qcow2", "-c", "write -P 0x5a 0 4096", str(disk)], check=True)
        clone = root / "clone.yaml"
        cfg["vm"]["name"] = name + "-clone"
        clone.write_text(yaml.safe_dump(cfg))
        yield name, root, disk, clone, cli
    finally:
        # Only this fixture's uniquely named domain and directory are owned here.
        assert_cleanup_safe(root / "state")
        for owned in (name + "-clone", name):
            if E2EHelper.domain_exists(owned):
                E2EHelper.virsh("destroy", owned, check=False)
                E2EHelper.virsh("undefine", owned, "--checkpoints-metadata", "--snapshots-metadata", "--nvram", check=False)
            E2EHelper.assert_domain_absent(owned)
        shutil.rmtree(root)


@pytest.mark.parametrize("recovery", ["checkpoint", "external-chain"])
def test_recovery_state_refusal_preserves_resources(recovery_domain, recovery):
    name, root, disk, clone, cli = recovery_domain
    if recovery == "checkpoint":
        E2EHelper.virsh("start", name)
        E2EHelper.virsh("checkpoint-create-as", name, "acceptance-baseline")
        # Force must refuse before stopping the domain.
        result = cli("reinstall", name, "--force", "--yes", check=False)
        assert result.returncode != 0 and "Backup checkpoints" in result.stderr
        assert E2EHelper.domain_state(name) == "running"
        E2EHelper.virsh("destroy", name)
        expected = "Backup checkpoints"
    else:
        overlay = root / "disks" / "external.qcow2"
        subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
            "-b", str(disk), str(overlay)], check=True)
        domain = ET.fromstring(E2EHelper.virsh("dumpxml", name, "--inactive").stdout)
        domain.find("./devices/disk[@device='disk']/source").set("file", str(overlay))
        xml = root / "external.xml"
        xml.write_text(ET.tostring(domain, encoding="unicode"))
        E2EHelper.virsh("define", str(xml))
        expected = "disk path"

    before = {p: digest(p) for p in root.rglob("*") if p.is_file()}
    xml_before = E2EHelper.virsh("dumpxml", name, "--inactive").stdout
    for args in [("clone", name, str(clone)), ("reinstall", name, "--yes"),
                 ("delete", name, "--yes"), ("delete", name, "--yes", "--keep-storage")]:
        result = cli(*args, check=False)
        assert result.returncode != 0 and expected in result.stderr, result.stderr
        assert E2EHelper.virsh("dumpxml", name, "--inactive").stdout == xml_before
        assert {p: digest(p) for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("filename", ["dependent.img", "dependent"])
def test_unattached_dependency_without_qcow2_suffix(recovery_domain, filename):
    name, root, disk, _, cli = recovery_domain
    overlay = root / "disks" / filename
    subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
        "-b", str(disk), str(overlay)], check=True)
    before = {p: digest(p) for p in root.rglob("*") if p.is_file()}
    xml_before = E2EHelper.virsh("dumpxml", name, "--inactive").stdout
    for args in [("delete", name, "--yes"), ("reinstall", name, "--yes")]:
        result = cli(*args, check=False)
        assert result.returncode != 0 and "dependent disk" in result.stderr, result.stderr
        assert E2EHelper.virsh("dumpxml", name, "--inactive").stdout == xml_before
        assert {p: digest(p) for p in root.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("operation", ["reinstall", "delete", "keep-storage"])
@pytest.mark.parametrize("data_format", ["raw", "qcow2"])
def test_lifecycle_with_data_disk(recovery_domain, operation, data_format):
    name, root, disk, _, cli = recovery_domain
    data = root / "data.img"
    subprocess.run(["qemu-img", "create", "-f", data_format, str(data), "16M"], check=True)
    subprocess.run(["qemu-io", "-f", data_format, "-c", "write -P 0x7b 0 4096", str(data)], check=True)
    device = ET.Element("disk", type="file", device="disk")
    ET.SubElement(device, "driver", name="qemu", type=data_format)
    ET.SubElement(device, "source", file=str(data))
    ET.SubElement(device, "target", dev="vdb", bus="virtio")
    xml = root / "data.xml"
    xml.write_text(ET.tostring(device, encoding="unicode"))
    E2EHelper.virsh("attach-device", name, str(xml), "--config")
    data_before, system_before = digest(data), digest(disk)
    if operation == "reinstall":
        cli("reinstall", name, "--yes", "--no-start")
        domain = ET.fromstring(E2EHelper.virsh("dumpxml", name, "--inactive").stdout)
        disks = domain.findall("./devices/disk[@device='disk']")
        assert len(disks) == 2
        assert any(d.find("source").get("file") == str(data) and d.find("target").get("dev") == "vdb" for d in disks)
    else:
        cli("delete", name, "--yes", *(["--keep-storage"] if operation == "keep-storage" else []))
        assert not E2EHelper.domain_exists(name)
        if operation == "keep-storage":
            assert digest(disk) == system_before
        else:
            assert not disk.exists()
    assert digest(data) == data_before
