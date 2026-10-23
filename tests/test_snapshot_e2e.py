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
import sys
import time
import uuid
from pathlib import Path

import pytest

from test_e2e import E2E_SSH_TIMEOUT, E2EHelper, build_vm_yaml, pytestmark, ssh_keypair  # noqa: F401
from test_recovery_e2e import digest

VM_DIR = Path(os.environ.get("KVM_VM_DISK_DIR", "/var/lib/libvirt/images/vm"))
STATE_DIR = Path(os.environ.get("KVM_VM_STATE_DIR", "/var/lib/kvm-vm/state"))
CLOUD_DIR = Path(os.environ.get("KVM_VM_CLOUD_DIR", "/var/lib/libvirt/images/cloud-init"))
DEF_DIR = Path(os.environ.get("KVM_VM_DEF_DIR", "/etc/kvm-vm/definitions"))
# Marker hashes, then the boot ID: an unchanged boot ID proves the guest did not reboot.
GUEST_CHECK = "sudo sha256sum /var/tmp/snap-*; cat /proc/sys/kernel/random/boot_id"


def kvm(*args: str, check: bool = True, env_extra=None) -> subprocess.CompletedProcess:
    result = E2EHelper.run_kvm_vm(*args, check=False, env_extra=env_extra)
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
    for owned in (name + "-c", name):
        if (STATE_DIR / "snapshots" / owned).exists():
            kvm("snapshot", "delete", owned, "--yes", check=False)
        kvm("delete", owned, "--force", "--yes", check=False)
        if E2EHelper.domain_exists(owned):
            E2EHelper.virsh("destroy", owned, check=False)
            E2EHelper.virsh("undefine", owned, "--snapshots-metadata", check=False)
        assert not E2EHelper.domain_exists(owned), f"Retaining files for domain that could not be removed: {owned}"
        for path in [*VM_DIR.glob(f"{owned}.*"), STATE_DIR / f"{owned}.json", DEF_DIR / f"{owned}.yaml"]:
            path.unlink(missing_ok=True)
        for directory in (CLOUD_DIR / owned, STATE_DIR / "snapshots" / owned):
            shutil.rmtree(directory, ignore_errors=True)


def boot_guest(name: str, definition: Path, key: Path) -> str:
    kvm("create", str(definition))
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    deadline = time.monotonic() + 120
    while E2EHelper.virsh("qemu-agent-command", name, '{"execute":"guest-ping"}', check=False).returncode:
        assert time.monotonic() < deadline, "guest agent did not respond"
        time.sleep(3)
    return ip


def test_snapshot_create_inspect_delete(snapshot_vm, ssh_keypair, tmp_path):
    name, definition = snapshot_vm
    key = ssh_keypair["private_key_path"]
    ip = boot_guest(name, definition, key)
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

    # A VM restarted on its Snapshot: deleting while it runs either works or stops with rollback
    # disabled (AppArmor, libvirt issue #806); offline deletion then works.
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
        assert "Rollback is disabled" in online.stderr and "shut the VM down" in online.stderr
        stopped = kvm("snapshot", "status", name, check=False)
        assert stopped.returncode == 1 and "needs attention" in stopped.stdout
        assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK) == written
        wait_off(name)
        kvm("snapshot", "delete", name, "--yes")
        E2EHelper.virsh("start", name)
        ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
        assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK).splitlines()[:-1] == written.splitlines()[:-1]
    assert_standalone(name, base)
    print("SNAPSHOT_E2E_PASSED", flush=True)


IDENTITY = ("cat /etc/machine-id; sudo cat /var/lib/cloud/data/instance-id; "
            "sudo sha256sum /etc/ssh/ssh_host_*_key.pub; hostname")


def persistent_config(name: str) -> tuple:
    xml = E2EHelper.virsh("dumpxml", name, "--inactive").stdout
    return (re.search(r"<vcpu[^>]*>(\d+)<", xml).group(1), re.search(r"<memory[^>]*>(\d+)<", xml).group(1),
            re.search(r"<uuid>([^<]+)<", xml).group(1), re.search(r"<mac address='([^']+)'", xml).group(1))


def test_snapshot_rollback(snapshot_vm, ssh_keypair):
    name, definition = snapshot_vm
    key = ssh_keypair["private_key_path"]
    ip = boot_guest(name, definition, key)
    base = VM_DIR / f"{name}.qcow2"
    managed = [DEF_DIR / f"{name}.yaml", STATE_DIR / f"{name}.json", *sorted((CLOUD_DIR / name).iterdir())]
    files_before = {path: path.read_bytes() for path in managed}
    captured = write_marker(ip, key, "a")
    identity = E2EHelper.ssh_exec(ip, key, IDENTITY)
    config = persistent_config(name)
    kvm("snapshot", "create", name)

    # An unwanted update: guest data, persistent vCPU/memory and the effective definition.
    write_marker(ip, key, "b")
    for args in (("setmaxmem", name, "1536M", "--config"), ("setmem", name, "1536M", "--config"),
                 ("setvcpus", name, "2", "--maximum", "--config"), ("setvcpus", name, "2", "--config")):
        E2EHelper.virsh(*args)
    effective = DEF_DIR / f"{name}.yaml"
    effective.write_text(effective.read_text().replace("vcpus: 1", "vcpus: 2"))
    assert persistent_config(name) != config

    unconfirmed = kvm("snapshot", "rollback", name, "--force", check=False)
    assert unconfirmed.returncode == 1 and "without --yes" in unconfirmed.stderr
    running = kvm("snapshot", "rollback", name, "--yes", check=False)
    assert running.returncode == 1 and "VM is running" in running.stderr
    assert E2EHelper.domain_state(name) == "running"

    started = time.monotonic()
    kvm("snapshot", "rollback", name, "--yes", "--force")
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    after = E2EHelper.ssh_exec(ip, key, GUEST_CHECK)
    print(f"SNAPSHOT_ROLLBACK_TO_SSH_SECONDS {time.monotonic() - started:.1f}", flush=True)
    assert after.splitlines()[:-1] == captured.splitlines()[:-1], "data must be the captured data only"
    assert after.splitlines()[-1] != captured.splitlines()[-1], "rollback restarts the guest"
    assert E2EHelper.ssh_exec(ip, key, IDENTITY) == identity
    assert E2EHelper.ssh_exec(ip, key, "nproc") == "1"
    assert persistent_config(name) == config
    assert {path: path.read_bytes() for path in managed} == files_before
    assert kvm("snapshot", "status", name).stdout.strip() == "Snapshot: none"
    assert re.search(rf"^{re.escape(name)}\s+running\s+yes", kvm("list", "--managed").stdout, re.M)
    kvm("validate", str(effective))
    assert_standalone(name, base)

    # The next Snapshot works, and a shut off VM rolls back without --force and can stay off.
    kvm("snapshot", "create", name)
    kvm("snapshot", "delete", name, "--yes")
    wait_off(name)
    kvm("snapshot", "create", name)
    E2EHelper.virsh("start", name)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    write_marker(ip, key, "c")
    wait_off(name)
    kvm("snapshot", "rollback", name, "--yes", "--no-start")
    assert E2EHelper.domain_state(name) == "shut off"
    E2EHelper.virsh("start", name)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK).splitlines()[:-1] == captured.splitlines()[:-1]
    assert_standalone(name, base)
    print("SNAPSHOT_ROLLBACK_E2E_PASSED", flush=True)


def assert_lifecycle_after_snapshot(name, keypair, tmp_path, expected_data):
    """Assert successful operations explicitly; fixture cleanup is not acceptance."""
    key = keypair["private_key_path"]
    wait_off(name)
    clone_name = name + "-c"
    clone_definition = tmp_path / "clone-after-snapshot.yaml"
    clone_definition.write_text(build_vm_yaml(clone_name, keypair["public_key_content"]), encoding="utf-8")
    kvm("clone", name, str(clone_definition))
    clone_ip = E2EHelper.wait_for_ssh(clone_name, key, timeout=E2E_SSH_TIMEOUT)
    E2EHelper.ssh_exec(clone_ip, key, "sudo cloud-init status --wait")
    assert E2EHelper.ssh_exec(clone_ip, key, GUEST_CHECK).splitlines()[:-1] == expected_data
    kvm("delete", clone_name, "--force", "--yes")
    assert not E2EHelper.domain_exists(clone_name)
    assert not (VM_DIR / f"{clone_name}.qcow2").exists()

    # Keep-storage on the original VM must preserve every managed file and disk byte.
    disk = VM_DIR / f"{name}.qcow2"
    manifest = STATE_DIR / f"{name}.json"
    files = [manifest, DEF_DIR / f"{name}.yaml", *sorted((CLOUD_DIR / name).iterdir())]
    before = {path: path.read_bytes() for path in files}
    checksum = digest(disk)
    xml = tmp_path / "kept-domain.xml"
    xml.write_text(E2EHelper.virsh("dumpxml", name, "--inactive").stdout, encoding="utf-8")
    kvm("delete", name, "--keep-storage", "--yes")
    assert not E2EHelper.domain_exists(name)
    assert digest(disk) == checksum
    assert {path: path.read_bytes() for path in files} == before

    # Put back the retained definition, then prove reinstall boots a fresh guest.
    E2EHelper.virsh("define", str(xml))
    previous_instance = json.loads(before[manifest])["instance_id"]
    kvm("reinstall", name, "--yes")
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    E2EHelper.ssh_exec(ip, key, "test -z \"$(find /var/tmp -maxdepth 1 -name 'snap-*' -print -quit)\"")
    assert json.loads(manifest.read_text())["instance_id"] != previous_instance
    kvm("validate", str(DEF_DIR / f"{name}.yaml"))
    assert re.search(rf"^{re.escape(name)}\s+running\s+yes", kvm("list", "--managed").stdout, re.M)
    kvm("delete", name, "--force", "--yes")
    assert not E2EHelper.domain_exists(name)
    assert not disk.exists() and not manifest.exists()
    assert not (CLOUD_DIR / name).exists() and not (DEF_DIR / f"{name}.yaml").exists()


@pytest.mark.parametrize("outcome", ["delete", "rollback", "failed-delete-recovered"])
def test_lifecycle_after_snapshot_recovery(snapshot_vm, ssh_keypair, tmp_path, outcome):
    name, definition = snapshot_vm
    key = ssh_keypair["private_key_path"]
    ip = boot_guest(name, definition, key)
    captured = write_marker(ip, key, "before").splitlines()[:-1]
    kvm("snapshot", "create", name)
    latest = write_marker(ip, key, "after").splitlines()[:-1]
    if outcome == "rollback":
        kvm("snapshot", "rollback", name, "--force", "--yes")
        expected = captured
    else:
        if outcome == "failed-delete-recovered":
            # Inject a deterministic failed live delete at the external-command boundary.
            # All other virsh calls, storage operations and subsequent recovery are real.
            real_virsh = shutil.which("virsh")
            assert real_virsh
            shim_dir = tmp_path / "fail-live-delete"
            shim_dir.mkdir()
            shim = shim_dir / "virsh"
            shim.write_text(
                f"#!{sys.executable}\nimport os, sys\n"
                f"if sys.argv[3:5] == ['snapshot-delete', {name!r}] and '--metadata' not in sys.argv:\n"
                "    sys.stderr.write('injected acceptance failure before live commit\\n')\n"
                "    sys.exit(1)\n"
                f"os.execv({real_virsh!r}, [{real_virsh!r}, *sys.argv[1:]])\n", encoding="utf-8")
            shim.chmod(0o700)
            result = kvm("snapshot", "delete", name, "--yes", check=False,
                         env_extra={"PATH": str(shim_dir) + os.pathsep + os.environ["PATH"]})
            assert result.returncode == 1 and "injected acceptance failure" in result.stderr
            assert "Rollback is disabled" in result.stderr
            assert kvm("snapshot", "status", name, check=False).returncode == 1
            refused = kvm("snapshot", "rollback", name, "--force", "--yes", check=False)
            assert refused.returncode == 1 and "Only a ready Snapshot" in refused.stderr
        wait_off(name)
        kvm("snapshot", "delete", name, "--yes")
        E2EHelper.virsh("start", name)
        expected = latest
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    assert E2EHelper.ssh_exec(ip, key, GUEST_CHECK).splitlines()[:-1] == expected
    assert_standalone(name, VM_DIR / f"{name}.qcow2")
    assert kvm("snapshot", "status", name).stdout.strip() == "Snapshot: none"
    assert_lifecycle_after_snapshot(name, ssh_keypair, tmp_path, expected)
    print(f"SNAPSHOT_LIFECYCLE_PASSED {outcome}: clone, keep-storage, reinstall, delete", flush=True)
