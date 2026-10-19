"""Real-KVM acceptance for the single short-lived Snapshot.

Run as root on a Linux KVM host:
    sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s

It creates one uniquely named e2e-snap-* guest from KVM_VM_E2E_DISTRO (default
debian12) and removes it afterward. It honours the KVM_VM_* directory overrides,
so it can run on a host whose default kvm-vm directories are in production use.
SNAPSHOT_* lines in the output are the measured evidence.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from test_e2e import E2E_SSH_TIMEOUT, E2EHelper, build_vm_yaml, pytestmark, ssh_keypair  # noqa: F401

VM_DIR = Path(os.environ.get("KVM_VM_DISK_DIR", "/var/lib/libvirt/images/vm"))
STATE_DIR = Path(os.environ.get("KVM_VM_STATE_DIR", "/var/lib/kvm-vm/state"))
CLOUD_DIR = Path(os.environ.get("KVM_VM_CLOUD_DIR", "/var/lib/libvirt/images/cloud-init"))
DEF_DIR = Path(os.environ.get("KVM_VM_DEF_DIR", "/etc/kvm-vm/definitions"))
# Marker hashes, then the boot ID: an unchanged boot ID proves the guest did not reboot.
GUEST_CHECK = "sudo sha256sum /var/tmp/snap-*; cat /proc/sys/kernel/random/boot_id"


def kvm(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = E2EHelper.run_kvm_vm(*args, check=False)
    print(f"$ kvm-vm {' '.join(args)} -> {result.returncode}\n{result.stdout}{result.stderr}", flush=True)
    if check:
        assert result.returncode == 0, result.stderr
    return result


def write_marker(ip: str, key: Path, label: str) -> str:
    E2EHelper.ssh_exec(ip, key, f"head -c 8388608 /dev/urandom | sudo tee /var/tmp/snap-{label}.bin >/dev/null; "
                                f"echo {label} | sudo tee /var/tmp/snap-{label} >/dev/null; sync")
    return E2EHelper.ssh_exec(ip, key, GUEST_CHECK)


def libvirt_snapshots(name: str) -> list:
    return E2EHelper.virsh("snapshot-list", name, "--name").stdout.split()


def wait_off(name: str) -> None:
    E2EHelper.virsh("shutdown", name)
    E2EHelper.wait_domain_state(name, "shut off", timeout=180)


def assert_standalone(name: str, base: Path) -> None:
    assert str(base) in E2EHelper.virsh("domblklist", name).stdout
    assert str(base) in E2EHelper.virsh("domblklist", name, "--inactive").stdout
    info = json.loads(subprocess.run(["qemu-img", "info", "--force-share", "--output=json", str(base)],
                                     check=True, text=True, capture_output=True).stdout)
    assert "backing-filename" not in info
    assert not libvirt_snapshots(name)
    assert not (STATE_DIR / "snapshots" / name).exists()
    assert sorted(VM_DIR.glob(f"{name}.*")) == [base]


@pytest.fixture
def snapshot_vm(tmp_path, ssh_keypair):
    name = f"e2e-snap-{uuid.uuid4().hex[:8]}"
    definition = tmp_path / f"{name}.yaml"
    definition.write_text(build_vm_yaml(name, ssh_keypair["public_key_content"]), encoding="utf-8")
    yield name, definition
    if (STATE_DIR / "snapshots" / name).exists():
        kvm("snapshot", "delete", name, "--yes", check=False)
    kvm("delete", name, "--force", "--yes", check=False)
    if E2EHelper.domain_exists(name):
        E2EHelper.virsh("destroy", name, check=False)
        E2EHelper.virsh("undefine", name, "--snapshots-metadata", check=False)
    for path in [*VM_DIR.glob(f"{name}.*"), STATE_DIR / f"{name}.json", DEF_DIR / f"{name}.yaml"]:
        path.unlink(missing_ok=True)
    for directory in (CLOUD_DIR / name, STATE_DIR / "snapshots" / name):
        shutil.rmtree(directory, ignore_errors=True)


def test_snapshot_create_inspect_delete(snapshot_vm, ssh_keypair, tmp_path):
    name, definition = snapshot_vm
    key = ssh_keypair["private_key_path"]
    kvm("create", str(definition))
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    deadline = time.monotonic() + 120
    while E2EHelper.virsh("qemu-agent-command", name, '{"execute":"guest-ping"}', check=False).returncode:
        assert time.monotonic() < deadline, "guest agent did not respond"
        time.sleep(3)
    base = VM_DIR / f"{name}.qcow2"
    before = write_marker(ip, key, "a")

    # Create while the guest runs: no reboot, data unchanged, overlay on top of the base.
    created = kvm("snapshot", "create", name)
    pause = re.search(r"frozen for (\d+) ms", created.stdout)
    assert pause, created.stdout
    print(f"SNAPSHOT_PAUSE_MS online {pause.group(1)}", flush=True)
    assert E2EHelper.domain_state(name) == "running"
    assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK) == before
    assert "ready (usable recovery point)" in kvm("snapshot", "status", name).stdout
    record = json.loads((STATE_DIR / "snapshots" / name / "record.json").read_text())
    overlay = Path(record["overlay"])
    chain = json.loads(subprocess.run(
        ["qemu-img", "info", "--force-share", "--backing-chain", "--output=json", str(overlay)],
        check=True, text=True, capture_output=True).stdout)
    assert [Path(image["filename"]) for image in chain] == [overlay, base]
    assert str(overlay) in E2EHelper.virsh("domblklist", name).stdout
    print(f"SNAPSHOT_CHAIN {overlay} -> {base}", flush=True)

    # One Snapshot per VM, and lifecycle commands refuse while it exists.
    second = kvm("snapshot", "create", name, check=False)
    assert second.returncode == 1 and "already has a Snapshot" in second.stderr
    assert libvirt_snapshots(name) == [record["snapshot"]]
    for args in (("delete", name, "--force", "--yes"), ("delete", name, "--keep-storage", "--force", "--yes"),
                 ("reinstall", name, "--force", "--yes")):
        refused = kvm(*args, check=False)
        assert refused.returncode == 1 and "kvm-vm Snapshot" in refused.stderr
    assert E2EHelper.domain_state(name) == "running"

    # Delete keeps the writes made after the Snapshot, still without a reboot.
    after = write_marker(ip, key, "b")
    started = time.monotonic()
    kvm("snapshot", "delete", name, "--yes")
    print(f"SNAPSHOT_DELETE_SECONDS online {time.monotonic() - started:.1f}", flush=True)
    assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK) == after
    assert not overlay.exists()
    assert_standalone(name, base)
    assert kvm("snapshot", "status", name).stdout.strip() == "Snapshot: none"

    # The next Snapshot works after cleanup, including a crash-consistent one.
    kvm("snapshot", "create", name)
    kvm("snapshot", "delete", name, "--yes")
    crash = kvm("snapshot", "create", name, "--no-quiesce")
    assert "crash-consistent" in crash.stdout
    kvm("snapshot", "delete", name, "--yes")
    assert_standalone(name, base)

    # Shut off: create without a freeze, clone refused, boot on the overlay, delete offline.
    wait_off(name)
    offline = kvm("snapshot", "create", name)
    assert "offline" in offline.stdout
    clone = tmp_path / "clone.yaml"
    clone.write_text(build_vm_yaml(name + "-c", ssh_keypair["public_key_content"]), encoding="utf-8")
    refused = kvm("clone", name, str(clone), check=False)
    assert refused.returncode == 1 and "kvm-vm Snapshot" in refused.stderr
    assert not E2EHelper.domain_exists(name + "-c")
    E2EHelper.virsh("start", name)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    written = write_marker(ip, key, "c")
    wait_off(name)
    kvm("snapshot", "delete", name, "--yes")
    assert_standalone(name, base)
    E2EHelper.virsh("start", name)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK).splitlines()[:-1] == written.splitlines()[:-1]

    # A VM restarted on its Snapshot: deleting while it runs either works or is refused
    # with the Snapshot unchanged (AppArmor, libvirt issue #806); offline deletion then works.
    kvm("snapshot", "create", name)
    wait_off(name)
    E2EHelper.virsh("start", name)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    written = write_marker(ip, key, "d")
    online = kvm("snapshot", "delete", name, "--yes", check=False)
    if online.returncode == 0:
        print("SNAPSHOT_DELETE_AFTER_RESTART online", flush=True)
        assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK) == written
    else:
        print("SNAPSHOT_DELETE_AFTER_RESTART refused online, then offline", flush=True)
        assert "The Snapshot is unchanged" in online.stderr and "Shut the VM down" in online.stderr
        assert "ready (usable recovery point)" in kvm("snapshot", "status", name).stdout
        assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK) == written
        wait_off(name)
        kvm("snapshot", "delete", name, "--yes")
        E2EHelper.virsh("start", name)
        ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
        assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK).splitlines()[:-1] == written.splitlines()[:-1]
    assert_standalone(name, base)
    print("SNAPSHOT_E2E_PASSED", flush=True)
