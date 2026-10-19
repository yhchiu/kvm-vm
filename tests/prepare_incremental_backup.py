#!/usr/bin/env python3
"""Prepare a retained full-plus-incremental Backup history through CLI and guest SSH.

Run as root on the dedicated Linux/KVM source host. It creates one unique guest,
then records three recovery points in one virtnbdbackup directory:

  point 0: full Backup
  point 1: guest data change, incremental Backup
  point 2: guest data change and a persistent definition change, incremental Backup

Each point has its own management artifacts and guest expectations so a later
Restore can pair the chosen disk state with matching configuration. The source
is left shut off. This is source preparation only; it is NOT cross-host
acceptance. See docs/full-backup-restore.md and docs/full-backup-acceptance.md.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from prepare_full_backup import digest, run
from recovery_checks import ChainError, check_backup_chain
from test_e2e import E2EHelper, build_vm_yaml

# Config of point 2 differs from points 0 and 1 so a mismatched pairing is visible.
POINT_CONFIG = {0: (1, 1024), 1: (1, 1024), 2: (2, 1536)}
IDENTITY_COMMAND = ("sha256sum /var/tmp/point-* ; cat /etc/machine-id; "
                    "sudo cat /var/lib/cloud/data/instance-id; "
                    "sudo sha256sum /etc/ssh/ssh_host_*_key.pub; hostname")


def write_point_data(ip, key, point):
    """Write identifiable data and force it to disk before the Backup."""
    command = ("head -c 4194304 /dev/urandom | sudo tee /var/tmp/point-%d.bin >/dev/null; "
               "printf 'point %d\\n' | sudo tee /var/tmp/point-%d >/dev/null; sync") % (point, point, point)
    E2EHelper.ssh_exec(ip, key, command)


def change_definition(name, definition_path, vcpus, memory_mib):
    """Change persistent libvirt configuration and effective YAML together."""
    run("virsh", "setmaxmem", name, "%dM" % memory_mib, "--config")
    run("virsh", "setmem", name, "%dM" % memory_mib, "--config")
    run("virsh", "setvcpus", name, str(vcpus), "--maximum", "--config")
    run("virsh", "setvcpus", name, str(vcpus), "--config")
    text = Path(definition_path).read_text(encoding="utf-8")
    text = re.sub(r"(?m)^(\s+vcpus:) \d+$", r"\g<1> %d" % vcpus, text)
    text = re.sub(r"(?m)^(\s+memory_mib:) \d+$", r"\g<1> %d" % memory_mib, text)
    Path(definition_path).write_text(text, encoding="utf-8")
    E2EHelper.run_kvm_vm("validate", definition_path)


def capture_management(name, state_path, state, destination):
    destination.mkdir(parents=True)
    xml = run("virsh", "dumpxml", name, "--inactive")
    domain = ET.fromstring(xml)
    if any(domain.find(path) is not None for path in ("os/loader", "os/nvram", "devices/tpm", "devices/hostdev")):
        raise RuntimeError("Guest outside the initial BIOS-only validation profile")
    disks = domain.findall("devices/disk[@device='disk']")
    if len(disks) != 1 or disks[0].find("source").get("file") != state["disk"]:
        raise RuntimeError("Unexpected disk topology")
    (destination / "domain.xml").write_text(xml, encoding="utf-8")
    (destination / "live.xml").write_text(run("virsh", "dumpxml", name), encoding="utf-8")
    (destination / "dominfo.txt").write_text(run("virsh", "dominfo", name), encoding="utf-8")
    (destination / "network.xml").write_text(run("virsh", "net-dumpxml", "default"), encoding="utf-8")
    shutil.copy2(state_path, destination / "state.json")
    shutil.copy2(state["definition"], destination / "effective.yaml")
    shutil.copytree(state["cloud_dir"], destination / "cloud")
    return xml


def backup_point(name, point, backup, socket_template, log):
    level = "full" if point == 0 else "inc"
    command = ["virtnbdbackup", "-d", name, "-l", level, "-o", str(backup)]
    if socket_template:
        socket = Path(socket_template.format(id=run("virsh", "domid", name).strip(),
                                             name20=name[:20], n=point))
        if socket.exists() or not socket.parent.is_dir():
            raise RuntimeError("Socket must be absent inside an existing permitted directory")
        command.extend(["-f", str(socket)])
    start = time.monotonic()
    with log.open("w") as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=subprocess.STDOUT)
    return time.monotonic() - start


def tree_digests(directory):
    return {p.relative_to(directory).as_posix(): digest(p) for p in sorted(directory.rglob("*")) if p.is_file()}


def clone_backup(source, target):
    """Copy a Backup directory, hard-linking only the large immutable data files."""
    shutil.copytree(source, target, copy_function=lambda s, d: (
        os.link(s, d) if s.endswith(".data") else shutil.copy2(s, d)))


def tool(*command):
    """Run a tool and return (exit status, combined output) without raising."""
    done = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout.replace("\r", "\n"))
    return done.returncode, "\n".join(line for line in plain.splitlines() if "(" not in line[:12])[-1500:]


def invalid_state_drill(name, key, recovery, root, socket_template):
    """Exercise tracking and history failures on copies; the retained set is untouched.

    Observations about virtnbdbackup/virtnbdrestore exit statuses are recorded, not
    asserted, because they describe the tested tool version. The assertions are the
    outcomes this project depends on: no false incremental success.
    """
    work = root / "drill"
    work.mkdir()
    backup = recovery / "backup"
    results = {}
    counter = iter(range(100))

    def incremental(directory, level="inc"):
        command = ["virtnbdbackup", "-d", name, "-l", level, "-o", str(directory)]
        if socket_template:
            command.extend(["-f", socket_template.format(
                id=run("virsh", "domid", name).strip(), name20=name[:20], n="drill%d" % next(counter))])
        return tool(*command)

    def data_files(directory):
        return sorted(p.name for p in directory.glob("*.data"))

    def checkpoints():
        return run("virsh", "checkpoint-list", name, "--name").split()

    # 1. Tracking survives a guest restart: an incremental Backup continues the history.
    run("virsh", "shutdown", name)
    E2EHelper.wait_domain_state(name, "shut off", timeout=120)
    run("virsh", "start", name)
    E2EHelper.wait_for_ssh(name, key, timeout=240)
    clone_backup(backup, work / "continue")
    status, output = incremental(work / "continue")
    assert status == 0 and "vda.inc.virtnbdbackup.3.data" in data_files(work / "continue"), output
    assert checkpoints()[-1] == "virtnbdbackup.3"
    results["incremental_after_guest_restart"] = {"exit": status, "checkpoints": checkpoints()}

    # 2. Libvirt forgot the newest checkpoint but its bitmap remains: refuse, write no data.
    run("virsh", "checkpoint-delete", name, "virtnbdbackup.3", "--metadata")
    clone_backup(backup, work / "forgotten")
    status, output = incremental(work / "forgotten")
    assert status != 0 and "vda.inc.virtnbdbackup.3.data" not in data_files(work / "forgotten"), output
    results["incremental_with_forgotten_checkpoint"] = {"exit": status, "output_tail": output[-300:]}

    # 3. A new full Backup in a new directory starts a new history and retires old checkpoints.
    status, output = incremental(work / "new-baseline", level="full")
    assert status == 0 and checkpoints() == ["virtnbdbackup.0"], (status, output, checkpoints())
    results["new_full_baseline"] = {"exit": status, "checkpoints_after": checkpoints()}

    # 4. The old directory can no longer continue: it must be refused, not extended.
    clone_backup(backup, work / "stale")
    status, output = incremental(work / "stale")
    assert status != 0 and "vda.inc.virtnbdbackup.3.data" not in data_files(work / "stale"), output
    assert "bitmap" in output.lower(), output
    results["incremental_into_retired_history"] = {"exit": status, "output_tail": output[-300:]}

    # 5. The new directory continues normally.
    clone_backup(work / "new-baseline", work / "new-baseline-continued")
    status, output = incremental(work / "new-baseline-continued")
    assert status == 0 and checkpoints() == ["virtnbdbackup.0", "virtnbdbackup.1"], (status, output)
    results["incremental_into_new_history"] = {"exit": status, "checkpoints_after": checkpoints()}

    # 6-9. History damage that virtnbdrestore does not refuse itself.
    sums = {line.split(None, 1)[1]: line.split(None, 1)[0]
            for line in (recovery / "SHA256SUMS").read_text().splitlines()}
    damage = {}

    def sealed_detects(directory):
        """True when the sealed checksums would flag this damaged copy of the Backup."""
        for relative, expected in sums.items():
            if not relative.startswith("backup/"):
                continue
            path = directory / relative[len("backup/"):]
            if not path.exists():
                return True
            if not (path.stat().st_ino == (recovery / relative).stat().st_ino) and digest(path) != expected:
                return True
        return False

    def damaged(label, mutate):
        directory = work / label
        clone_backup(backup, directory)
        mutate(directory)
        return directory

    def restore(directory, *extra):
        output_dir = work / ("out-" + directory.name)
        status, text = tool("virtnbdrestore", "-i", str(directory), "-o", str(output_dir), *extra)
        shutil.rmtree(output_dir, ignore_errors=True)
        return status, text

    def drop_increment(directory):
        (directory / "vda.inc.virtnbdbackup.1.data").unlink()

    def corrupt_increment(directory):
        path = directory / "vda.inc.virtnbdbackup.2.data"
        content = bytearray(path.read_bytes())
        path.unlink()
        content[100000] ^= 0xFF
        path.write_bytes(bytes(content))

    cases = {
        "missing_increment": (drop_increment, "virtnbdbackup.2"),
        "corrupted_increment": (corrupt_increment, "virtnbdbackup.2"),
        "unknown_recovery_point": (lambda directory: None, "virtnbdbackup.9"),
        "missing_checkpoint_list": (lambda directory: next(directory.glob("*.cpt")).unlink(), None),
    }
    for label, (mutate, until) in cases.items():
        directory = damaged(label, mutate)
        extra = ["--until", until] if until else []
        verify_status, _ = tool("virtnbdrestore", "-i", str(directory), "-o", "verify")
        restore_status, _ = restore(directory, *extra)
        try:
            check_backup_chain(directory, until)
            chain_refused = False
        except ChainError:
            chain_refused = True
        digest_mismatch = sealed_detects(directory)
        damage[label] = {"tool_verify_exit": verify_status, "tool_restore_exit": restore_status,
                         "chain_check_refused": chain_refused,
                         "sealed_checksums_detect_it": digest_mismatch}
        if label in ("missing_increment", "unknown_recovery_point", "missing_checkpoint_list"):
            assert chain_refused, label
        if label in ("missing_increment", "corrupted_increment", "missing_checkpoint_list"):
            assert digest_mismatch, label
        if label == "corrupted_increment":
            assert verify_status != 0
    results["damaged_history"] = damage
    shutil.rmtree(work)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-template",
                        help="Permitted socket path with {id}, {name20} and {n} placeholders")
    parser.add_argument("--invalid-state-drill", action="store_true",
                        help="After recording the points, exercise invalid tracking and damaged history on copies")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run on the authorized Linux/KVM host as root")
    for command in ("virtnbdbackup", "virtnbdrestore", "virsh", "ssh-keygen"):
        if not shutil.which(command):
            raise RuntimeError("Missing prerequisite: " + command)
    os.environ["LC_ALL"] = "C"
    os.umask(0o077)
    name = "e2e-inc-" + uuid.uuid4().hex[:12]
    root = Path("/var/lib/libvirt/images") / name
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    for variable, folder in (("KVM_VM_DISK_DIR", "disks"),
                             ("KVM_VM_CLOUD_DIR", "cloud"),
                             ("KVM_VM_STATE_DIR", "state"),
                             ("KVM_VM_DEF_DIR", "definitions")):
        os.environ[variable] = str(root / folder)
    print("Retained test root: " + str(root), flush=True)
    key = root / "guest-key"
    run("ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key))
    definition = root / "input.yaml"
    definition.write_text(build_vm_yaml(name, key.with_suffix(".pub").read_text()), encoding="utf-8")
    E2EHelper.run_kvm_vm("validate", str(definition))
    os.umask(0o022)
    try:
        E2EHelper.run_kvm_vm("create", str(definition), capture=False)
    finally:
        os.umask(0o077)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=360)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    state_path = root / "state" / (name + ".json")
    state = json.loads(state_path.read_text())
    recovery = root / "recovery-set"
    backup = recovery / "backup"
    points = recovery / "points"
    points.mkdir(parents=True)
    versions = {"virtnbdbackup": run("virtnbdbackup", "--version"),
                "virtnbdrestore": run("virtnbdrestore", "--version"),
                "libvirt": run("virsh", "version"), "qemu": run("qemu-img", "--version"),
                "source_host_id": Path("/etc/machine-id").read_text(),
                "consistency": "synced marker files; inspect backup logs for freeze warnings",
                "name": name, "source_root": str(root), "points": POINT_CONFIG}
    (recovery / "guest-identity-command.txt").write_text(IDENTITY_COMMAND + "\n", encoding="utf-8")
    (recovery / "environment.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")
    seconds = {}
    for point, (vcpus, memory) in sorted(POINT_CONFIG.items()):
        if point > 0:
            write_point_data(ip, key, point)
        if (vcpus, memory) != POINT_CONFIG[max(point - 1, 0)]:
            change_definition(name, state["definition"], vcpus, memory)
        if point == 0:
            write_point_data(ip, key, 0)
        directory = points / str(point)
        xml = capture_management(name, state_path, state, directory / "management")
        # The running guest keeps its boot-time CPUs, so config.json records the persistent point config.
        expected = E2EHelper.ssh_exec(ip, key, IDENTITY_COMMAND)
        (directory / "guest-expected.txt").write_text(expected + "\n", encoding="utf-8")
        (directory / "config.json").write_text(json.dumps({"vcpus": vcpus, "memory_mib": memory}) + "\n")
        seconds[point] = backup_point(name, point, backup, args.socket_template,
                                      directory / "backup-console.log")
        checkpoints = run("virsh", "checkpoint-list", name, "--name").split()
        (directory / "checkpoints.txt").write_text("\n".join(checkpoints) + "\n", encoding="utf-8")
        if len(checkpoints) != point + 1:
            raise RuntimeError("Unexpected checkpoint list after point %d: %r" % (point, checkpoints))
        assert xml == run("virsh", "dumpxml", name, "--inactive")
        assert E2EHelper.ssh_exec(ip, key, IDENTITY_COMMAND) == expected
        assert run("virsh", "domstate", name).strip() == "running"
        print("POINT %d RECORDED in %.1f s" % (point, seconds[point]), flush=True)
    (recovery / "backup-seconds.json").write_text(json.dumps(seconds, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["virtnbdrestore", "-i", str(backup), "-o", "verify"], check=True)
    (recovery / "backup-info.txt").write_text(
        run("virtnbdrestore", "-i", str(backup), "-o", "dump"), encoding="utf-8")
    E2EHelper.run_kvm_vm("status", name, capture=False)
    sums = [digest(p) + "  " + p.relative_to(recovery).as_posix()
            for p in sorted(recovery.rglob("*")) if p.is_file()]
    (recovery / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=recovery, check=True,
                   stdout=subprocess.DEVNULL)
    if args.invalid_state_drill:
        results = invalid_state_drill(name, key, recovery, root, args.socket_template)
        (root / "drill-results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print("INVALID-STATE DRILL PASSED: " + json.dumps(results["damaged_history"]), flush=True)
        # The retained set must not change because of the drill.
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=recovery, check=True,
                       stdout=subprocess.DEVNULL)
    run("virsh", "autostart", name, "--disable")
    if run("virsh", "domstate", name).strip() != "shut off":
        run("virsh", "shutdown", name)
    deadline = time.monotonic() + 120
    while run("virsh", "domstate", name).strip() != "shut off":
        if time.monotonic() > deadline:
            raise RuntimeError("Guest did not shut down; fence before target boot")
        time.sleep(2)
    print("SOURCE PREPARATION PASSED; source shut off, autostart disabled.", flush=True)
    print("Recovery set is still local, NOT independent disaster protection: " + str(recovery), flush=True)


if __name__ == "__main__":
    main()
