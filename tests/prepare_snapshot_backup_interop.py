#!/usr/bin/env python3
"""Exercise Snapshots together with full and incremental Backups on the source host.

Run as root on the dedicated Linux/KVM source host. It creates one unique guest
and runs the supported sequence from docs/snapshots.md:

  1. An old Backup history (full + incremental) with libvirt checkpoints.
  2. Snapshot creation is refused while the checkpoints exist.
  3. The tracking is retired; the old history stays restorable but cannot continue.
  4. A Backup job running during Snapshot creation, and a killed Backup, are
     refused by kvm-vm and cleaned up with the documented steps.
  5. Snapshot, unwanted update (data and configuration), rollback.
  6. Snapshot, kept update, Snapshot delete.
  7. A new history (full + incremental) with per-point management artifacts,
     sealed for restore on another host with tests/restore_recovery_point.py.
  8. Lifecycle commands refuse the VM while the new tracking exists.

The source is left shut off. Results go to ROOT/interop-results.json.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import uuid

from prepare_full_backup import digest, run
from prepare_incremental_backup import (IDENTITY_COMMAND, backup_point, capture_management, change_definition,
                                        clone_backup, tool, write_point_data)
from recovery_checks import check_backup_chain
from test_e2e import E2EHelper, build_vm_yaml


def kvm(*args):
    result = E2EHelper.run_kvm_vm(*args, check=False)
    print(f"$ kvm-vm {' '.join(args)} -> {result.returncode}\n{result.stdout}{result.stderr}", flush=True)
    return result.returncode, result.stdout + result.stderr


def checkpoints(name):
    return run("virsh", "checkpoint-list", name, "--name").split()


def bitmaps(name, disk):
    """Persistent dirty bitmaps; a running QEMU writes them to the image only when it closes it."""
    if E2EHelper.domain_state(name) == "running":
        nodes = json.loads(run("virsh", "qemu-monitor-command", name,
                               '{"execute":"query-named-block-nodes","arguments":{"flat":true}}'))["return"]
        # A backup job adds temporary bitmaps without a name.
        return sorted({b.get("name", "<anonymous>") for node in nodes for b in node.get("dirty-bitmaps", [])})
    info = json.loads(run("qemu-img", "info", "--output=json", str(disk)))
    return sorted(b["name"] for b in info.get("format-specific", {}).get("data", {}).get("bitmaps", []))


def guest_files(ip, key):
    return E2EHelper.ssh_exec(ip, key, "ls /var/tmp/ | grep '^point-' | grep -v bin | tr '\\n' ' '").split()


def socket_args(name, template, label):
    if not template:
        return []
    return ["-f", template.format(id=run("virsh", "domid", name).strip(), name20=name[:20], n=label)]


def backup_job_active(name):
    # domjobinfo fails for a moment while libvirt starts a backup job.
    for _ in range(30):
        job = subprocess.run(["virsh", "domjobinfo", name], text=True, capture_output=True)
        if job.returncode == 0:
            return not re.search(r"Job type:\s+None", job.stdout)
        time.sleep(1)
    raise RuntimeError("virsh domjobinfo kept failing: " + job.stderr.strip())


def start_backup(name, target, template, label):
    command = ["virtnbdbackup", "-d", name, "-l", "full", "-o", str(target)] + socket_args(name, template, label)
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while not backup_job_active(name):
        assert process.poll() is None, "backup finished before its job was seen"
        assert time.monotonic() < deadline, "backup job did not start"
        time.sleep(0.5)
    return process


def record_point(name, ip, key, state_path, state, recovery, point, template, config):
    directory = recovery / "points" / str(point)
    capture_management(name, state_path, state, directory / "management")
    expected = E2EHelper.ssh_exec(ip, key, IDENTITY_COMMAND)
    (directory / "guest-expected.txt").write_text(expected + "\n", encoding="utf-8")
    (directory / "config.json").write_text(json.dumps({"vcpus": config[0], "memory_mib": config[1]}) + "\n")
    seconds = backup_point(name, point, recovery / "backup", template, directory / "backup-console.log")
    (directory / "checkpoints.txt").write_text("\n".join(checkpoints(name)) + "\n", encoding="utf-8")
    print(f"POINT {point} RECORDED in {recovery.name} ({seconds:.1f} s)", flush=True)
    return seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-template", help="Permitted socket path with {id}, {name20} and {n} placeholders")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run on the authorized Linux/KVM host as root")
    for command in ("virtnbdbackup", "virtnbdrestore", "virsh", "ssh-keygen"):
        if not shutil.which(command):
            raise RuntimeError("Missing prerequisite: " + command)
    os.environ["LC_ALL"] = "C"
    os.umask(0o077)
    name = "e2e-sbi-" + uuid.uuid4().hex[:12]
    root = Path("/var/lib/libvirt/images") / name
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    for variable, folder in (("KVM_VM_DISK_DIR", "disks"), ("KVM_VM_CLOUD_DIR", "cloud"),
                             ("KVM_VM_STATE_DIR", "state"), ("KVM_VM_DEF_DIR", "definitions")):
        os.environ[variable] = str(root / folder)
    print("Retained test root: " + str(root), flush=True)
    key = root / "guest-key"
    run("ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key))
    definition = root / "input.yaml"
    definition.write_text(build_vm_yaml(name, key.with_suffix(".pub").read_text()), encoding="utf-8")
    os.umask(0o022)
    try:
        assert kvm("create", str(definition))[0] == 0
    finally:
        os.umask(0o077)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=360)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    state_path = root / "state" / (name + ".json")
    state = json.loads(state_path.read_text())
    disk = Path(state["disk"])
    template = args.socket_template
    results = {"name": name, "versions": {
        "virtnbdbackup": run("virtnbdbackup", "--version").strip(),
        "libvirt": run("virsh", "version", "--daemon").strip().splitlines()[-1]}}

    # 1. Old history with checkpoints.
    old = root / "old-history"
    (old / "points").mkdir(parents=True)
    (old / "guest-identity-command.txt").write_text(IDENTITY_COMMAND + "\n", encoding="utf-8")
    for point in (0, 1):
        write_point_data(ip, key, point)
        record_point(name, ip, key, state_path, state, old, point, template, (1, 1024))
    assert checkpoints(name) == ["virtnbdbackup.0", "virtnbdbackup.1"]
    results["old_history"] = {"checkpoints": checkpoints(name), "bitmaps": bitmaps(name, disk)}

    # 2. A Snapshot is refused while the tracking exists, and nothing changes.
    code, out = kvm("snapshot", "create", name)
    assert code == 1 and "checkpoint-delete" in out and "nothing was changed" in out, out
    assert checkpoints(name) == ["virtnbdbackup.0", "virtnbdbackup.1"] and len(bitmaps(name, disk)) == 2
    assert not (root / "state" / "snapshots" / name).exists()
    results["snapshot_refused_with_checkpoints"] = out.strip().splitlines()[-1][:300]

    # 3. Retire the tracking as documented; the old history stays restorable but cannot continue.
    run("virsh", "checkpoint-delete", name, "virtnbdbackup.0", "--children")
    assert checkpoints(name) == [] and bitmaps(name, disk) == []
    verify_status, _ = tool("virtnbdrestore", "-i", str(old / "backup"), "-o", "verify")
    assert verify_status == 0 and check_backup_chain(old / "backup") == ["virtnbdbackup.0", "virtnbdbackup.1"]
    stale = root / "drill-stale"
    clone_backup(old / "backup", stale)
    status, out = tool("virtnbdbackup", "-d", name, "-l", "inc", "-o", str(stale), *socket_args(name, template, "stale"))
    assert status != 0 and not list(stale.glob("*.inc.virtnbdbackup.2.data")), out
    assert checkpoints(name) == [] and bitmaps(name, disk) == []
    results["after_retire"] = {"old_history_verify": verify_status, "incremental_into_old_history": status,
                               "reason": out.strip().splitlines()[-1][-200:]}

    # 4a. A Backup job is running while kvm-vm is asked for a Snapshot.
    conflict = root / "drill-conflict"
    process = start_backup(name, conflict, template, "conflict")
    code, out = kvm("snapshot", "create", name)
    refusal = out.strip().splitlines()[-1][:200]
    assert code == 1 and ("Backup checkpoints" in out or "Conflicting recovery operation" in out), out
    backup_exit = process.wait(timeout=600)
    assert not (root / "state" / "snapshots" / name).exists()
    run("virsh", "checkpoint-delete", name, "virtnbdbackup.0", "--children")
    assert checkpoints(name) == [] and bitmaps(name, disk) == []
    shutil.rmtree(conflict)
    results["snapshot_during_backup_job"] = {"kvm_vm_exit": code, "refusal": refusal, "backup_exit": backup_exit}

    # 4b. A Backup killed in the middle leaves a job and a checkpoint; kvm-vm refuses until both are cleaned.
    killed = root / "drill-killed"
    process = start_backup(name, killed, template, "killed")
    process.send_signal(signal.SIGKILL)
    process.wait()
    left = {"job_active": backup_job_active(name), "checkpoints": checkpoints(name), "bitmaps": bitmaps(name, disk)}
    code, out = kvm("snapshot", "create", name)
    assert code == 1, out
    after_failure = {}
    for args_ in (("reinstall", name, "--force", "--yes"), ("delete", name, "--keep-storage", "--force", "--yes"),
                  ("delete", name, "--force", "--yes")):
        lifecycle_code, lifecycle_out = kvm(*args_)
        assert lifecycle_code == 1 and re.search(
            r"Backup checkpoints|Conflicting recovery operation|Cannot inspect domain jobs", lifecycle_out), lifecycle_out
        after_failure[" ".join(a for a in args_ if a.startswith("--keep") or not a.startswith("-"))] = \
            lifecycle_out.strip().splitlines()[-1][:120]
    assert E2EHelper.domain_state(name) == "running" and disk.is_file()
    if backup_job_active(name):
        run("virsh", "domjobabort", name)
    deadline = time.monotonic() + 60
    while backup_job_active(name):
        assert time.monotonic() < deadline, "backup job did not end after domjobabort"
        time.sleep(1)
    if checkpoints(name):
        run("virsh", "checkpoint-delete", name, checkpoints(name)[0], "--children")
    assert checkpoints(name) == [] and bitmaps(name, disk) == []
    shutil.rmtree(killed)
    results["killed_backup"] = {"left_behind": left, "kvm_vm_exit": code,
                                "refusal": out.strip().splitlines()[-1][:200], "lifecycle": after_failure}

    # 5. Snapshot, unwanted update of data and configuration, rollback.
    code, out = kvm("snapshot", "create", name)
    assert code == 0, out
    pause = int(re.search(r"frozen for (\d+) ms", out).group(1))
    write_point_data(ip, key, 2)
    change_definition(name, state["definition"], 2, 1536)
    started = time.monotonic()
    code, out = kvm("snapshot", "rollback", name, "--yes", "--force")
    assert code == 0, out
    ip = E2EHelper.wait_for_ssh(name, key, timeout=360)
    rollback_seconds = time.monotonic() - started
    assert guest_files(ip, key) == ["point-0", "point-1"]
    assert E2EHelper.ssh_exec(ip, key, "nproc") == "1"
    assert checkpoints(name) == [] and bitmaps(name, disk) == []
    results["rollback"] = {"freeze_ms": pause, "to_ssh_seconds": round(rollback_seconds, 1),
                           "guest_files": guest_files(ip, key)}

    # 6. Snapshot, kept update, delete.
    assert kvm("snapshot", "create", name)[0] == 0
    write_point_data(ip, key, 3)
    assert kvm("snapshot", "delete", name, "--yes")[0] == 0
    assert guest_files(ip, key) == ["point-0", "point-1", "point-3"]

    # 7. The next history: a new full Backup and an incremental, sealed for restore elsewhere.
    recovery = root / "recovery-set"
    (recovery / "points").mkdir(parents=True)
    (recovery / "guest-identity-command.txt").write_text(IDENTITY_COMMAND + "\n", encoding="utf-8")
    seconds = {0: record_point(name, ip, key, state_path, state, recovery, 0, template, (1, 1024))}
    write_point_data(ip, key, 4)
    seconds[1] = record_point(name, ip, key, state_path, state, recovery, 1, template, (1, 1024))
    assert checkpoints(name) == ["virtnbdbackup.0", "virtnbdbackup.1"]
    assert check_backup_chain(recovery / "backup") == ["virtnbdbackup.0", "virtnbdbackup.1"]
    subprocess.run(["virtnbdrestore", "-i", str(recovery / "backup"), "-o", "verify"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (recovery / "backup-info.txt").write_text(run("virtnbdrestore", "-i", str(recovery / "backup"), "-o", "dump"))
    (recovery / "backup-seconds.json").write_text(json.dumps(seconds) + "\n")
    sums = [digest(p) + "  " + p.relative_to(recovery).as_posix() for p in sorted(recovery.rglob("*")) if p.is_file()]
    (recovery / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    results["new_history"] = {"backup_seconds": seconds, "files": len(sums),
                              "expected_guest_files": guest_files(ip, key)}

    # 8. Lifecycle commands refuse the VM while the new tracking exists.
    lifecycle = {}
    for args_ in (("snapshot", "create", name), ("reinstall", name, "--force", "--yes"),
                  ("delete", name, "--keep-storage", "--force", "--yes")):
        code, out = kvm(*args_)
        assert code == 1 and "Backup checkpoints" in out, out
        lifecycle[" ".join(args_[:2])] = code
    run("virsh", "autostart", name, "--disable")
    run("virsh", "shutdown", name)
    E2EHelper.wait_domain_state(name, "shut off", timeout=180)
    clone = root / "clone.yaml"
    clone.write_text(build_vm_yaml(name + "-c", key.with_suffix(".pub").read_text()), encoding="utf-8")
    code, out = kvm("clone", name, str(clone))
    assert code == 1 and "Backup checkpoints" in out, out
    lifecycle["clone"] = code
    results["lifecycle_with_new_tracking"] = lifecycle
    (root / "interop-results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print("INTEROP RESULTS: " + json.dumps(results), flush=True)
    print("SOURCE INTEROPERABILITY PASSED; source shut off, autostart disabled: " + str(recovery), flush=True)


if __name__ == "__main__":
    main()
