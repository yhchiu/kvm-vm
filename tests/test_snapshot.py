"""Snapshot create, inspect and delete through the command handlers.

Only host programs are simulated. The simulated libvirt follows what libvirt
11.10 did on a real host: an external disk-only snapshot moves the live and
persistent disk to a new overlay backed by the original disk, libvirt itself
would accept a second snapshot, and deleting the snapshot commits the overlay
into the original disk and removes the overlay file.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import signal
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "kvm-vm"
_loader = SourceFileLoader("kvm_vm_snapshot", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("kvm_vm_snapshot", _loader)
kvm_vm = importlib.util.module_from_spec(_spec)
sys.modules["kvm_vm_snapshot"] = kvm_vm
_spec.loader.exec_module(kvm_vm)


class Host:
    """One managed VM, test01, on a simulated libvirt host."""

    def __init__(self, root: Path, monkeypatch):
        for key in ("BASE_DIR", "VM_DIR", "STATE_DIR", "CLOUD_DIR", "DEF_DIR"):
            directory = root / key.lower()
            directory.mkdir()
            monkeypatch.setattr(kvm_vm, key, directory)
        image = kvm_vm.BASE_DIR / "base.qcow2"
        image.write_bytes(b"cloud image")
        self.base = kvm_vm.VM_DIR / "test01.qcow2"
        self.base.write_bytes(b"data at the recovery point")
        cloud = kvm_vm.CLOUD_DIR / "test01"
        cloud.mkdir()
        self.seed = cloud / "seed.img"
        self.seed.write_bytes(b"seed")
        cfg = kvm_vm.normalize_definition({
            "version": 1, "vm": {"name": "test01"},
            "storage": {"image": {"path": str(image)}},
            "network": {"mode": "nat", "libvirt_network": "default", "mac": "52:54:00:99:88:77",
                        "ipv4": {"method": "dhcp"}},
            "cloud_init": {"ssh_authorized_keys": ["ssh-ed25519 AAAA test@test"]},
        })
        self.definition = kvm_vm.DEF_DIR / "test01.yaml"
        kvm_vm.save_state(cfg, self.base, {}, "original-instance", self.definition)
        self.manifest = kvm_vm.STATE_DIR / "test01.json"
        self.root = root
        self.power = "running"
        self.vcpus = 1
        self.top, self.backing = self.base, None
        self.snapshots: list = []
        self.frozen = self.ever_frozen = False
        self.agent = True
        self.checkpoints = ""
        self.daemon = "11.10.0"
        self.job = "None"
        self.os_extra = self.extra_devices = ""
        self.disk_info: dict = {}
        self.fail: dict = {}
        self.interrupt: dict = {}
        self.kill: dict = {}  # SystemExit stands in for kvm-vm being killed
        self.signals: dict = {}
        self.partial_commit = False
        self.leave_overlay_on_failure = False
        monkeypatch.setattr(kvm_vm.shutil, "which", lambda name: None if name == "restorecon" else name)
        monkeypatch.setattr(kvm_vm.subprocess, "run", self.run)

    # Guest view: writes after a Snapshot land in the overlay.
    def write(self, data: bytes) -> None:
        self.top.write_bytes(data)

    def guest_data(self) -> bytes:
        if self.backing is not None and self.top.read_bytes() != b"":
            return self.top.read_bytes()
        return self.base.read_bytes()

    def files(self) -> dict:
        return {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}

    def xml(self) -> str:
        # Like libvirt: a running domain shows its chain, a shut off one leaves it to the image.
        backing = ""
        if self.power != "shut off":
            backing = (f'<backingStore type="file"><format type="qcow2"/><source file="{self.backing}"/>'
                       f'<backingStore/></backingStore>' if self.backing else "<backingStore/>")
        return (f'<domain><name>test01</name><uuid>uuid-1</uuid><vcpu>{self.vcpus}</vcpu>'
                f'<os>{self.os_extra}</os><devices>'
                f'<disk type="file" device="disk"><driver type="qcow2"/><source file="{self.top}"/>{backing}'
                f'<target dev="vda"/></disk>'
                f'<disk type="file" device="cdrom"><source file="{self.seed}"/><target dev="sda"/><readonly/></disk>'
                f'{self.extra_devices}</devices></domain>')

    def run(self, cmd, **kwargs):
        code, stdout, stderr = 0, "", ""
        action = cmd[3] if cmd[0] == "virsh" else cmd[1]
        if self.interrupt.get(action) == "before":
            raise KeyboardInterrupt
        if self.kill.get(action) == "before":
            raise SystemExit("kvm-vm killed")
        if action in ("domfsfreeze", "snapshot-create-as", "domfsthaw", "snapshot-list"):
            self.signals[action] = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
        if action in self.fail:
            code, stderr = 1, self.fail[action]
        elif action == "commit":
            # qemu-img commit writes the overlay into its backing file and empties the overlay.
            self.base.write_bytes(self.guest_data())
            Path(cmd[-1]).write_bytes(b"")
        elif action == "define":
            root = kvm_vm.ET.parse(cmd[4]).getroot()
            self.top = Path(root.find("./devices/disk[@device='disk']/source").get("file"))
            self.vcpus = int(root.findtext("vcpu"))
            if self.top == self.base:
                self.backing = None
        elif action == "destroy":
            self.power = "shut off"
        elif action == "start":
            self.power = "running"
        elif cmd[0] == "qemu-img":
            info = {"format": "qcow2", "virtual-size": 10 * 1024**3}
            path = Path(cmd[-1])
            if self.backing is not None and path == self.top:
                info["backing-filename"] = str(self.backing)
            if path == self.base:
                info.update(self.disk_info)
            stdout = json.dumps(info)
        elif action == "dominfo":
            code = 0 if cmd[4] == "test01" else 1
        elif action == "domstate":
            stdout = self.power
        elif action == "version":
            stdout = f"Using library: libvirt {self.daemon}\nRunning against daemon: {self.daemon}"
        elif action == "snapshot-list":
            stdout = "\n".join(self.snapshots)
        elif action == "checkpoint-list":
            stdout = self.checkpoints
        elif action == "domjobinfo":
            stdout = "Job type: " + self.job
        elif action == "blockjob":
            pass
        elif action == "dumpxml":
            stdout = self.xml()
        elif action == "list":
            stdout = "test01"
        elif action in ("net-info", "net-dumpxml"):
            stdout = "Active: yes" if action == "net-info" else '<network><forward mode="nat"/></network>'
        elif action in ("domblklist", "domiflist"):
            stdout = "-"
        elif action == "domifaddr":
            code = 1
        elif action == "qemu-agent-command":
            code, stdout, stderr = (0, '{"return":{}}', "") if self.agent else (1, "", "Guest agent is not responding")
        elif action == "domfsfreeze":
            self.frozen = self.ever_frozen = True
            stdout = "Froze 2 filesystem(s)"
        elif action == "domfsthaw":
            self.frozen = False
            stdout = "Thawed 2 filesystem(s)"
        elif action == "snapshot-create-as":
            overlay = Path(re.search(r"vda,snapshot=external,file=(.+)", " ".join(cmd)).group(1).split(" --")[0])
            assert "sda,snapshot=no" in cmd, "the cloud-init CD-ROM must be excluded"
            overlay.write_bytes(b"")
            self.top, self.backing = overlay, self.base
            self.snapshots.append(cmd[5])
        elif action == "snapshot-delete" and self.partial_commit:
            # The live commit wrote part of the newer data into the base, then ran out of space.
            self.base.write_bytes(b"part of the newer data")
            code, stderr = 1, "error: block commit failed while deleting disk 'vda' snapshot: 'No space left on device'"
        elif action == "snapshot-delete" and "--metadata" in cmd:
            self.snapshots.remove(cmd[5])
        elif action == "snapshot-delete":
            assert self.power == "running", "offline deletion is kvm-vm's qemu-img commit"
            self.base.write_bytes(self.guest_data())
            self.top.unlink()
            self.top, self.backing = self.base, None
            self.snapshots.remove(cmd[5])
        else:
            raise AssertionError(cmd)
        if action in self.fail and action == "snapshot-create-as" and self.leave_overlay_on_failure:
            Path(re.search(r"file=(\S+)", " ".join(cmd)).group(1)).write_bytes(b"")
        if self.interrupt.get(action) == "after":
            raise KeyboardInterrupt
        if self.kill.get(action) == "after":
            raise SystemExit("kvm-vm killed")
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, cmd, stdout, stderr)
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)


@pytest.fixture
def host(tmp_path, monkeypatch):
    return Host(tmp_path, monkeypatch)


def create(no_quiesce=False, dry_run=False):
    return kvm_vm.cmd_snapshot_create(argparse.Namespace(name="test01", no_quiesce=no_quiesce, dry_run=dry_run))


def delete():
    return kvm_vm.cmd_snapshot_delete(argparse.Namespace(name="test01", yes=True))


def status(capsys):
    code = kvm_vm.cmd_snapshot_status(argparse.Namespace(name="test01"))
    return code, capsys.readouterr().out


def record():
    return json.loads((kvm_vm.snapshot_dir("test01") / "record.json").read_text())


@pytest.mark.parametrize("field,value", [
    ("base", []), ("overlay", {}), ("overlay", "bad\x00path"),
    ("snapshot", None), ("snapshot", " "), ("status", []), ("status", "future-state"),
    ("guest_state", 42), ("consistency", False), ("warnings", "warning"),
    ("warnings", [None]), ("problem", {}), ("pause_ms", True), ("pause_ms", -1),
    ("pause_ms", "10"), ("version", True), ("version", 2), ("vm", "another-vm"),
])
def test_malformed_record_reports_recovery_guidance_without_mutation(host, capsys, field, value):
    create()
    saved = record()
    saved[field] = value
    kvm_vm.save_snapshot_record("test01", saved)
    before = host.files()
    snapshots = list(host.snapshots)
    code, report = status(capsys)
    assert code == 1
    assert "unreadable" in report
    assert "virsh snapshot-list" in report
    for command in (kvm_vm.cmd_snapshot_delete, kvm_vm.cmd_snapshot_rollback):
        with pytest.raises(kvm_vm.KVMError):
            command(argparse.Namespace(name="test01", yes=True, force=True))
    assert host.files() == before
    assert host.snapshots == snapshots


def test_create_keeps_the_guest_running_and_records_a_coherent_recovery_point(host, capsys):
    definition, manifest = host.definition.read_bytes(), host.manifest.read_bytes()
    assert create() == 0
    out = capsys.readouterr().out
    assert host.power == "running" and not host.frozen and host.ever_frozen
    saved = record()
    assert saved["status"] == "ready" and saved["consistency"] == "filesystems frozen"
    assert isinstance(saved["pause_ms"], int) and "frozen for" in out
    assert host.top == Path(saved["overlay"]) and host.backing == host.base
    assert host.base.read_bytes() == b"data at the recovery point"
    captured = kvm_vm.snapshot_dir("test01")
    assert "<name>test01</name>" in (captured / "domain.xml").read_text()
    assert (captured / "effective.yaml").read_bytes() == definition
    assert (captured / "state.json").read_bytes() == manifest
    assert (captured / "cloud" / "seed.img").read_bytes() == b"seed"


def test_delete_keeps_writes_made_after_the_snapshot_and_allows_the_next_one(host, capsys):
    create()
    overlay = host.top
    host.write(b"update applied after the snapshot")
    assert delete() == 0
    assert host.base.read_bytes() == b"update applied after the snapshot"
    assert host.top == host.base and host.backing is None
    assert not overlay.exists() and not kvm_vm.snapshot_dir("test01").exists()
    assert "current guest data is kept" in capsys.readouterr().out
    assert create() == 0 and record()["status"] == "ready"


def test_second_snapshot_is_refused_and_the_first_is_unchanged(host):
    create()
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match="already has a Snapshot"):
        create()
    assert host.files() == before and len(host.snapshots) == 1


def test_status_distinguishes_none_ready_and_foreign_snapshots(host, capsys):
    assert status(capsys) == (0, "Snapshot: none\n")
    create()
    code, out = status(capsys)
    assert code == 0 and "ready (usable recovery point)" in out and "kvm-vm snapshot delete test01" in out
    delete()
    host.snapshots.append("made-with-virsh")
    code, out = status(capsys)
    assert code == 1 and "none created by kvm-vm" in out and "made-with-virsh" in out


def test_vm_status_shows_the_snapshot_section(host, capsys):
    create()
    capsys.readouterr()
    assert kvm_vm.cmd_status(argparse.Namespace(name="test01")) == 0
    assert re.search(r"Snapshot\n-+\nSnapshot: +kvm-vm-", capsys.readouterr().out)


def test_failure_after_freeze_thaws_the_guest_and_creates_nothing(host):
    before = host.files()
    host.fail["snapshot-create-as"] = "internal error: unable to execute QEMU command"
    with pytest.raises(kvm_vm.KVMError, match="No Snapshot was created.*thawed"):
        create()
    assert host.ever_frozen and not host.frozen
    assert host.files() == before and host.top == host.base and not host.snapshots


def test_ctrl_c_and_sigterm_are_ignored_while_the_guest_is_frozen(host):
    assert create() == 0
    # snapshot-list here is the check after the thaw, the last step before the record is ready.
    for action in ("domfsfreeze", "snapshot-create-as", "domfsthaw", "snapshot-list"):
        assert host.signals[action] == (signal.SIG_IGN, signal.SIG_IGN), action
    assert signal.getsignal(signal.SIGINT) is not signal.SIG_IGN, "handlers are put back afterwards"
    assert not host.frozen and record()["status"] == "ready"


@pytest.mark.parametrize("when", ["before", "after"])
def test_kvm_vm_killed_during_creation_leaves_a_record_for_the_operator(host, capsys, when):
    host.kill["snapshot-create-as"] = when
    with pytest.raises(SystemExit):
        create()
    host.kill.clear()
    code, out = status(capsys)
    assert code == 1 and "needs attention" in out and "Creation stopped" in out
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    if when == "after":  # libvirt made the Snapshot: delete merges it and keeps the current data
        host.write(b"current data")
    assert delete() == 0
    assert not kvm_vm.snapshot_dir("test01").exists() and not host.snapshots and host.top == host.base
    assert host.base.read_bytes() == (b"current data" if when == "after" else b"data at the recovery point")


def test_failed_creation_that_left_an_overlay_is_reported_and_cleaned_on_request(host, capsys):
    host.fail["snapshot-create-as"] = "disk full"
    host.leave_overlay_on_failure = True
    with pytest.raises(kvm_vm.KVMError, match="libvirt or storage changed"):
        create()
    overlay = Path(record()["overlay"])
    assert overlay.exists() and host.top == host.base
    code, out = status(capsys)
    assert code == 1 and "Snapshot creation failed: disk full" in out and "Creation stopped" in out
    assert delete() == 0
    assert not overlay.exists() and host.base.read_bytes() == b"data at the recovery point"


def test_thaw_failure_is_reported_with_recovery_guidance(host, capsys):
    host.fail["domfsthaw"] = "guest agent timeout"
    with pytest.raises(kvm_vm.KVMError, match="was created, but thawing.*domfsthaw test01"):
        create()
    assert host.frozen
    saved = record()
    assert saved["status"] == "ready" and "guest agent timeout" in saved["warnings"][0]
    code, out = status(capsys)
    assert "Warning: thawing guest filesystems failed" in out
    assert "may still be frozen" in out


def test_freeze_failure_creates_nothing(host):
    before = host.files()
    host.fail["domfsfreeze"] = "guest agent error"
    with pytest.raises(kvm_vm.KVMError, match="could not freeze.*--no-quiesce"):
        create()
    assert host.files() == before and not host.snapshots


def test_unresponsive_agent_is_refused_unless_crash_consistency_is_requested(host):
    host.agent = False
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match="guest agent.*--no-quiesce"):
        create()
    assert host.files() == before and not host.snapshots
    assert create(no_quiesce=True) == 0
    saved = record()
    assert saved["consistency"] == "crash-consistent" and "pause_ms" not in saved
    assert not host.ever_frozen


def test_shut_off_vm_is_snapshot_without_a_freeze(host):
    host.power = "shut off"
    assert create() == 0
    assert record()["consistency"] == "offline" and not host.ever_frozen
    host.write(b"offline change")
    assert delete() == 0 and host.base.read_bytes() == b"offline change"


def test_dry_run_changes_nothing(host, capsys):
    before = host.files()
    assert create(dry_run=True) == 0
    assert "Would create Snapshot" in capsys.readouterr().out
    assert host.files() == before and not host.snapshots


def unmanaged(host):
    host.manifest.unlink()


def lock_held(host):
    (kvm_vm.STATE_DIR / ".lifecycle-lock").mkdir()


@pytest.mark.parametrize("change, message", [
    (lambda h: setattr(h, "checkpoints", "virtnbdbackup.0"), "Backup checkpoints"),
    (lambda h: h.snapshots.append("made-with-virsh"), "already has a Snapshot"),
    (lambda h: h.disk_info.update({"backing-filename": "/images/template.qcow2"}), "recovery data"),
    (lambda h: h.disk_info.update({"format": "raw"}), "Snapshots require a qcow2"),
    (lambda h: setattr(h, "extra_devices", f'<disk type="file" device="disk"><source file="{h.base}"/>'
                      '<target dev="vdb"/></disk>'), "Snapshots require exactly one"),
    (lambda h: h.disk_info.update({"format-specific": {"data": {"bitmaps": [{"name": "b"}]}}}), "recovery data"),
    (lambda h: setattr(h, "os_extra", "<loader>/usr/share/OVMF/OVMF_CODE.fd</loader>"), "UEFI"),
    (lambda h: setattr(h, "extra_devices", '<disk type="file" device="floppy"><target dev="fda"/></disk>'),
     "Unsupported floppy"),
    (lambda h: setattr(h, "daemon", "9.8.0"), "libvirt 9.9"),
    (lambda h: setattr(h, "power", "paused"), "running or shut off"),
    (lambda h: setattr(h, "job", "Backup"), "recovery operation"),
    (unmanaged, "managed VMs"),
    (lock_held, "lifecycle operation"),
])
def test_unsupported_or_conflicting_states_are_refused_before_any_change(host, change, message):
    change(host)
    before = host.files()
    snapshots = list(host.snapshots)
    with pytest.raises(kvm_vm.KVMError, match=message):
        create()
    assert host.files() == before and host.snapshots == snapshots and not host.ever_frozen
    assert not kvm_vm.snapshot_dir("test01").exists()


@pytest.mark.parametrize("operation, keep_storage", [
    ("delete", False), ("delete", True), ("reinstall", False), ("clone", False)])
def test_lifecycle_commands_refuse_a_vm_with_a_snapshot(host, tmp_path, operation, keep_storage):
    host.power = "shut off"
    create()
    clone = tmp_path / "clone.yaml"
    kvm_vm.yaml_dump(clone, dict(kvm_vm.yaml_load(host.definition), vm={"name": "clone01"}))
    before = host.files()
    args = argparse.Namespace(name="test01", source="test01", definition=str(clone), yes=True, force=True,
                              keep_storage=keep_storage, dry_run=False, refresh_image=False, no_start=True)
    with pytest.raises(kvm_vm.KVMError, match="kvm-vm Snapshot"):
        getattr(kvm_vm, "cmd_" + operation)(args)
    assert host.files() == before and len(host.snapshots) == 1


@pytest.mark.parametrize("when", ["before", "after"])
def test_interrupted_deletion_stops_and_a_new_delete_finishes_it(host, capsys, when):
    create()
    host.write(b"keep me")
    host.interrupt["snapshot-delete"] = when
    with pytest.raises(kvm_vm.KVMError, match="Snapshot deletion stopped"):
        delete()
    host.interrupt.clear()
    code, out = status(capsys)
    assert code == 1 and "needs attention" in out and "Rollback is disabled" in out
    assert not (kvm_vm.STATE_DIR / ".lifecycle-lock").exists()
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    assert delete() == 0
    assert host.base.read_bytes() == b"keep me" and host.top == host.base
    assert not kvm_vm.snapshot_dir("test01").exists() and not host.snapshots


def test_running_delete_refused_by_libvirt_disables_rollback_until_an_offline_delete(host, capsys):
    create()
    host.write(b"keep me")
    host.fail["snapshot-delete"] = ("internal error: unable to execute QEMU command 'block-commit': "
                                    "Could not open base: Permission denied")
    with pytest.raises(kvm_vm.KVMError, match="(?s)Permission denied.*Rollback is disabled.*AppArmor"):
        delete()
    assert record()["status"] == "deleting" and host.guest_data() == b"keep me"
    assert status(capsys)[0] == 1
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    host.fail.clear()
    host.power = "shut off"
    assert delete() == 0
    assert host.base.read_bytes() == b"keep me" and host.top == host.base
    assert sorted(kvm_vm.VM_DIR.iterdir()) == [host.base] and not host.snapshots


def test_failed_live_commit_that_wrote_part_of_the_base_cannot_be_rolled_back(host):
    create()
    host.write(b"newer data")
    host.partial_commit = True
    with pytest.raises(kvm_vm.KVMError, match="(?s)No space left on device.*Rollback is disabled"):
        delete()
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    assert host.guest_data() == b"newer data", "the current guest view is intact"
    host.partial_commit = False
    assert delete() == 0 and host.base.read_bytes() == b"newer data"


@pytest.mark.parametrize("step", ["commit", "define"])
def test_interrupted_offline_delete_stops_for_the_operator(host, capsys, step):
    host.power = "shut off"
    create()
    host.write(b"keep me")
    host.interrupt[step] = "after"
    with pytest.raises(kvm_vm.KVMError, match="Snapshot deletion stopped"):
        delete()
    host.interrupt.clear()
    assert host.guest_data() == b"keep me"
    code, out = status(capsys)
    assert code == 1 and "Rollback is disabled" in out
    if step == "define":  # the VM is back on its disk; only libvirt's metadata is left
        with pytest.raises(kvm_vm.KVMError, match="cannot be deleted in its current state"):
            delete()
        host.snapshots.clear()  # the operator runs: virsh snapshot-delete NAME SNAPSHOT --metadata
    assert delete() == 0
    assert host.base.read_bytes() == b"keep me" and host.top == host.base and not host.snapshots
    assert sorted(kvm_vm.VM_DIR.iterdir()) == [host.base]
    assert not kvm_vm.snapshot_dir("test01").exists()


@pytest.mark.parametrize("dependent", ["image backed by the overlay", "file qemu-img cannot read"])
@pytest.mark.parametrize("relative", [False, True])
def test_leftover_overlay_is_kept_unless_proven_unused(host, caplog, monkeypatch, dependent, relative):
    host.fail["snapshot-create-as"] = "disk full"
    host.leave_overlay_on_failure = True
    with pytest.raises(kvm_vm.KVMError):
        create()
    overlay = Path(record()["overlay"])
    other = kvm_vm.VM_DIR / "other.qcow2"
    other.write_bytes(b"other image")
    original_run = host.run

    def run(cmd, **kwargs):
        if cmd[:2] == ["qemu-img", "info"] and Path(cmd[-1]) == other:
            if dependent.startswith("file"):
                raise subprocess.CalledProcessError(1, cmd, "", "unknown format")
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"backing-filename": overlay.name if relative else str(overlay)}), "")
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(kvm_vm.subprocess, "run", run)
    assert delete() == 0
    assert overlay.exists() and "was kept" in caplog.text
    assert not kvm_vm.snapshot_dir("test01").exists()


def test_delete_refuses_a_snapshot_changed_outside_kvm_vm(host):
    create()
    host.snapshots.append("made-with-virsh")
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match="cannot be deleted in its current state"):
        delete()
    assert host.files() == before and len(host.snapshots) == 2


def test_snapshot_not_made_by_kvm_vm_points_to_virsh_not_to_kvm_vm_delete(host):
    host.snapshots.append("made-with-virsh")
    with pytest.raises(kvm_vm.KVMError) as error:
        create()
    assert "did not create" in str(error.value) and "resolved with virsh" in str(error.value)
    assert "Delete it with" not in str(error.value)


def test_delete_refuses_backup_checkpoints_created_after_the_snapshot(host):
    create()
    host.write(b"current data")
    host.checkpoints = "virtnbdbackup.0"
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match="Backup checkpoints.*not changed"):
        delete()
    assert host.files() == before and len(host.snapshots) == 1 and record()["status"] == "ready"


@pytest.mark.parametrize("operation", ["create", "delete", "rollback"])
def test_checkpoint_refusals_name_the_supported_sequence(host, operation):
    if operation != "create":
        create()
    host.checkpoints = "virtnbdbackup.0\nvirtnbdbackup.1"
    before = host.files()
    with pytest.raises(kvm_vm.KVMError) as error:
        {"create": create, "delete": delete, "rollback": rollback}[operation]()
    message = str(error.value)
    assert "virtnbdbackup.0, virtnbdbackup.1" in message
    assert "nothing was changed" in message or "were not changed" in message
    assert "checkpoint-delete test01 virtnbdbackup.0 --children" in message and "new full Backup" in message
    assert host.files() == before and host.power == "running"


def test_job_query_failure_while_a_backup_starts_is_an_explicit_refusal(host):
    host.fail["domjobinfo"] = "error: Timed out during operation: cannot acquire state change lock"
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match="Cannot inspect domain jobs.*Retry"):
        create()
    assert host.files() == before and not host.ever_frozen and not host.snapshots


def test_delete_without_a_snapshot_is_refused(host):
    with pytest.raises(kvm_vm.KVMError, match="no kvm-vm Snapshot"):
        delete()


def rollback(yes=True, force=True, no_start=False):
    return kvm_vm.cmd_snapshot_rollback(argparse.Namespace(name="test01", yes=yes, force=force, no_start=no_start))


def change_after_snapshot(host):
    """An unwanted update: guest data, persistent config, definition and a cloud-init file."""
    host.write(b"unwanted update")
    host.vcpus = 2
    host.definition.write_text(host.definition.read_text().replace("vcpus: 1", "vcpus: 2"))
    host.seed.write_bytes(b"edited seed")


def test_rollback_restores_disk_configuration_and_management_files(host, capsys):
    originals = {p: host.files()[p] for p in (host.definition, host.manifest, host.seed)}
    create()
    overlay = host.top
    change_after_snapshot(host)
    assert rollback() == 0
    out = capsys.readouterr().out
    assert host.guest_data() == b"data at the recovery point" and host.top == host.base and host.backing is None
    assert host.vcpus == 1 and host.power == "running"
    assert {p: p.read_bytes() for p in originals} == originals
    assert not overlay.exists() and not host.snapshots and not kvm_vm.snapshot_dir("test01").exists()
    assert "used up" in out and "until the VM started" in out
    assert status(capsys) == (0, "Snapshot: none\n")
    assert create() == 0


def test_rollback_of_a_shut_off_vm_can_leave_it_off(host):
    host.power = "shut off"
    create()
    change_after_snapshot(host)
    assert rollback(force=False, no_start=True) == 0
    assert host.power == "shut off" and host.guest_data() == b"data at the recovery point"


@pytest.mark.parametrize("kwargs, message", [
    ({"yes": False}, "without --yes"),
    ({"force": False}, "VM is running"),
])
def test_rollback_needs_confirmation_and_force_for_a_running_vm(host, kwargs, message):
    create()
    change_after_snapshot(host)
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match=message):
        rollback(**kwargs)
    assert host.files() == before and host.power == "running" and record()["status"] == "ready"


@pytest.mark.parametrize("change, message", [
    (lambda h: setattr(h, "checkpoints", "virtnbdbackup.0"), "Backup checkpoints"),
    (lambda h: (kvm_vm.snapshot_dir("test01") / "domain.xml").write_text(
        h.xml().replace("uuid-1", "another-vm")), "captured configuration"),
    (lambda h: h.snapshots.append("made-with-virsh"), "Only a ready Snapshot"),
])
def test_rollback_refuses_unsafe_states_before_any_change(host, change, message):
    create()
    change_after_snapshot(host)
    change(host)
    before = host.files()
    with pytest.raises(kvm_vm.KVMError, match=message):
        rollback()
    assert host.files() == before and host.power == "running" and host.guest_data() == b"unwanted update"


def test_rollback_without_a_kvm_vm_snapshot_is_refused(host):
    with pytest.raises(kvm_vm.KVMError, match="no kvm-vm Snapshot"):
        rollback()
    host.snapshots.append("made-with-virsh")
    with pytest.raises(kvm_vm.KVMError, match="none created by kvm-vm"):
        rollback()


@pytest.mark.parametrize("step", ["destroy", "define", "snapshot-delete"])
def test_interrupted_rollback_stops_and_the_operator_finishes_it(host, capsys, step):
    originals = {p: p.read_bytes() for p in (host.definition, host.manifest, host.seed)}
    create()
    overlay = host.top
    change_after_snapshot(host)
    host.interrupt[step] = "after"
    with pytest.raises(kvm_vm.KVMError, match="Rollback stopped"):
        rollback()
    host.interrupt.clear()
    code, out = status(capsys)
    assert code == 1 and "does not continue a rollback" in out
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    with pytest.raises(kvm_vm.KVMError, match="kvm-vm Snapshot"):
        kvm_vm.cmd_delete(argparse.Namespace(name="test01", yes=True, force=True, keep_storage=True))
    captured = kvm_vm.snapshot_dir("test01")
    if step != "snapshot-delete":
        with pytest.raises(kvm_vm.KVMError, match="cannot be deleted in its current state"):
            delete()
        assert overlay.exists(), "the discarded data stays until the operator finishes"
        # The documented manual steps: put back the files, define the captured XML, drop the metadata.
        for source, target in ((captured / "effective.yaml", host.definition), (captured / "state.json", host.manifest),
                               (captured / "cloud" / "seed.img", host.seed)):
            shutil.copy2(source, target)
        host.run(["virsh", "-c", "qemu:///system", "define", str(captured / "domain.xml")])
        host.snapshots.clear()
    assert delete() == 0
    assert host.guest_data() == b"data at the recovery point" and host.vcpus == 1
    assert {p: p.read_bytes() for p in originals} == originals
    assert not overlay.exists() and not captured.exists()


def test_failed_redefine_stops_the_rollback(host, capsys):
    create()
    change_after_snapshot(host)
    host.fail["define"] = "error: XML error"
    with pytest.raises(kvm_vm.KVMError, match="Rollback stopped: error: XML error"):
        rollback()
    assert record()["status"] == "rolling-back" and host.backing == host.base
    host.fail.clear()
    with pytest.raises(kvm_vm.KVMError, match="Only a ready Snapshot"):
        rollback()
    assert "docs/snapshots.md" in status(capsys)[1]


def test_record_removal_cannot_leave_a_partial_record(host, monkeypatch, capsys):
    create()
    host.write(b"kept")
    real_rmtree = kvm_vm.shutil.rmtree

    def interrupted(path, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(kvm_vm.shutil, "rmtree", interrupted)
    with pytest.raises(KeyboardInterrupt):
        delete()
    monkeypatch.setattr(kvm_vm.shutil, "rmtree", real_rmtree)
    capsys.readouterr()
    assert status(capsys) == (0, "Snapshot: none\n")
    assert host.base.read_bytes() == b"kept" and create() == 0


def test_unreadable_record_is_removed_once_nothing_is_left(host, capsys):
    kvm_vm.snapshot_dir("test01").mkdir(parents=True)  # for example a crash right after creating it
    code, out = status(capsys)
    assert code == 1 and "unreadable" in out and "removes the leftover record" in out
    with pytest.raises(kvm_vm.KVMError, match="kvm-vm Snapshot"):
        kvm_vm.cmd_delete(argparse.Namespace(name="test01", yes=True, force=True, keep_storage=True))
    assert delete() == 0
    capsys.readouterr()
    assert status(capsys) == (0, "Snapshot: none\n") and create() == 0


def test_start_failure_after_rollback_is_reported_as_completed_rollback(host, capsys):
    create()
    change_after_snapshot(host)
    host.fail["start"] = "error: internal error"
    with pytest.raises(kvm_vm.KVMError, match="Rollback completed, but starting"):
        rollback()
    assert host.power == "shut off" and host.guest_data() == b"data at the recovery point"
    capsys.readouterr()
    assert status(capsys) == (0, "Snapshot: none\n")


def test_snapshot_commands_parse():
    parser = kvm_vm.build_parser()
    args = parser.parse_args(["snapshot", "create", "web01", "--no-quiesce"])
    assert args.func is kvm_vm.cmd_snapshot_create and args.no_quiesce
    assert parser.parse_args(["snapshot", "status", "web01"]).func is kvm_vm.cmd_snapshot_status
    assert parser.parse_args(["snapshot", "delete", "web01", "--yes"]).yes
    args = parser.parse_args(["snapshot", "rollback", "web01", "--yes", "--force", "--no-start"])
    assert args.func is kvm_vm.cmd_snapshot_rollback and args.yes and args.force and args.no_start
