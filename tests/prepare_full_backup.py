#!/usr/bin/env python3
"""Prepare a retained, source-only full-backup drill through CLI and guest SSH.

Run as root on the dedicated Linux/KVM test host. This deliberately leaves the
source guest shut off and retains its artifacts for the separate target phase.
It is NOT a passing cross-host acceptance test. See docs/full-backup-acceptance.md.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

from test_e2e import E2EHelper, build_vm_yaml
from file_helpers import digest


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-template", help="Optional permitted socket path with {id} and {name20} placeholders")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run on the authorized Linux/KVM host as root")
    for command in ("virtnbdbackup", "virtnbdrestore", "virsh", "ssh-keygen"):
        if not shutil.which(command):
            raise RuntimeError("Missing prerequisite: " + command)
    os.environ["LC_ALL"] = "C"
    os.umask(0o077)
    name = "e2e-full-" + uuid.uuid4().hex[:12]
    root = Path("/var/lib/libvirt/images") / name
    root.mkdir(mode=0o755)
    # QEMU must traverse the test root; keys and management remain private.
    root.chmod(0o755)
    for variable, folder in (("KVM_VM_DISK_DIR", "disks"),
                             ("KVM_VM_CLOUD_DIR", "cloud"),
                             ("KVM_VM_STATE_DIR", "state"),
                             ("KVM_VM_DEF_DIR", "definitions")):
        os.environ[variable] = str(root / folder)
    print("Retained test root: " + str(root), flush=True)
    print("Only source preparation; cross-host acceptance remains pending.", flush=True)
    key = root / "guest-key"
    run("ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key))
    definition = root / "input.yaml"
    definition.write_text(build_vm_yaml(name, key.with_suffix(".pub").read_text()), encoding="utf-8")
    E2EHelper.run_kvm_vm("validate", str(definition))
    # Match the normal CLI's directory traversal permissions for QEMU.
    os.umask(0o022)
    try:
        E2EHelper.run_kvm_vm("create", str(definition), capture=False)
    finally:
        os.umask(0o077)
    ip = E2EHelper.wait_for_ssh(name, key, timeout=360)
    E2EHelper.ssh_exec(ip, key, "sudo cloud-init status --wait")
    E2EHelper.ssh_exec(ip, key, "printf '%s\\n' " + name + " | sudo tee /var/tmp/recovery-marker; sync")
    identity_command = ("sha256sum /var/tmp/recovery-marker; cat /etc/machine-id; "
                        "sudo cat /var/lib/cloud/data/instance-id; "
                        "sudo sha256sum /etc/ssh/ssh_host_*_key.pub; hostname")
    recovery = root / "recovery-set"
    management = recovery / "management"
    management.mkdir(parents=True)
    expected = E2EHelper.ssh_exec(ip, key, identity_command)
    (management / "guest-expected.txt").write_text(expected + "\n", encoding="utf-8")
    (management / "guest-identity-command.txt").write_text(identity_command + "\n", encoding="utf-8")
    state_path = root / "state" / (name + ".json")
    state = json.loads(state_path.read_text())
    xml = run("virsh", "dumpxml", name, "--inactive")
    domain = ET.fromstring(xml)
    if any(domain.find(path) is not None for path in ("os/loader", "os/nvram", "devices/tpm", "devices/hostdev")):
        raise RuntimeError("Guest outside the initial BIOS-only validation profile")
    disks = domain.findall("devices/disk[@device='disk']")
    if len(disks) != 1 or disks[0].find("source").get("file") != state["disk"]:
        raise RuntimeError("Unexpected disk topology")
    (management / "domain.xml").write_text(xml, encoding="utf-8")
    (management / "live.xml").write_text(run("virsh", "dumpxml", name), encoding="utf-8")
    (management / "network.xml").write_text(run("virsh", "net-dumpxml", "default"), encoding="utf-8")
    shutil.copy2(state_path, management / "state.json")
    shutil.copy2(state["definition"], management / "effective.yaml")
    shutil.copytree(state["cloud_dir"], management / "cloud")
    versions = {"virtnbdbackup": run("virtnbdbackup", "--version"),
                "virtnbdrestore": run("virtnbdrestore", "--version"),
                "libvirt": run("virsh", "version"),
                "qemu": run("qemu-img", "--version"),
                "source_host_id": Path("/etc/machine-id").read_text(),
                "consistency": "synced marker; inspect backup log for freeze warnings",
                "name": name, "source_root": str(root)}
    (management / "environment.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")
    before = {p.relative_to(root / "cloud" / name): digest(p)
              for p in (root / "cloud" / name).rglob("*") if p.is_file()}
    start = time.monotonic()
    backup_command = ["virtnbdbackup", "-d", name, "-l", "full", "-o", str(recovery / "backup")]
    if args.socket_template:
        socket = args.socket_template.format(id=run("virsh", "domid", name).strip(), name20=name[:20])
        if Path(socket).exists() or not Path(socket).parent.is_dir():
            raise RuntimeError("Socket must be absent inside an existing permitted directory")
        backup_command.extend(["-f", socket])
    with (recovery / "backup-console.log").open("w") as log:
        subprocess.run(backup_command, check=True, stdout=log, stderr=subprocess.STDOUT)
    (recovery / "backup-seconds.txt").write_text(str(time.monotonic() - start) + "\n", encoding="utf-8")
    subprocess.run(["virtnbdrestore", "-i", str(recovery / "backup"), "-o", "verify"], check=True)
    assert xml == run("virsh", "dumpxml", name, "--inactive")
    assert state_path.read_bytes() == (management / "state.json").read_bytes()
    assert Path(state["definition"]).read_bytes() == (management / "effective.yaml").read_bytes()
    assert before == {p.relative_to(root / "cloud" / name): digest(p)
                      for p in (root / "cloud" / name).rglob("*") if p.is_file()}
    assert expected == E2EHelper.ssh_exec(ip, key, identity_command)
    assert run("virsh", "domstate", name).strip() == "running"
    E2EHelper.run_kvm_vm("status", name, capture=False)
    run("virsh", "autostart", name, "--disable")
    run("virsh", "shutdown", name)
    deadline = time.monotonic() + 120
    while run("virsh", "domstate", name).strip() != "shut off":
        if time.monotonic() > deadline:
            raise RuntimeError("Guest did not shut down; fence before target boot")
        time.sleep(2)
    sums = [digest(p) + "  " + p.relative_to(recovery).as_posix()
            for p in sorted(recovery.rglob("*")) if p.is_file()]
    (recovery / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=recovery, check=True)
    print("SOURCE PREPARATION PASSED; source shut off, autostart disabled.", flush=True)
    print("Recovery set is still local, NOT independent disaster protection: " + str(recovery), flush=True)


if __name__ == "__main__":
    main()
