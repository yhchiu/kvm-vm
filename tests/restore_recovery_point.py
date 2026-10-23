#!/usr/bin/env python3
"""Restore one recovery point of a retained Backup set on a destination host.

Run as root on the replacement KVM host with kvm-vm installed and the sealed set
already transferred. The script follows docs/full-backup-restore.md: verify the
set, reconstruct the chosen point, pair it with that point's management
artifacts, map host-specific paths, define and boot the guest, then check data,
identity, persistent configuration and kvm-vm management. It never reads the
source host. It leaves the restored guest running so the operator can continue.

Supported sets: either a full-only set written by tests/prepare_full_backup.py
(use --single) or a point-based set written by tests/prepare_incremental_backup.py.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

KEY_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
               "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "LogLevel=ERROR"]


def run(*args, check=True, capture=True):
    return subprocess.run(args, check=check, text=True,
                          capture_output=capture).stdout if capture else subprocess.run(args, check=check)


def virsh(*args, check=True):
    return run("virsh", "-c", "qemu:///system", *args, check=check)


def host_machine_and_emulator():
    """Return the destination's q35 machine type and QEMU binary from libvirt."""
    caps = ET.fromstring(virsh("capabilities"))
    for guest in caps.findall("guest"):
        arch = guest.find("arch")
        if guest.findtext("os_type") != "hvm" or arch is None or arch.get("name") != "x86_64":
            continue
        emulator = arch.findtext("emulator")
        for machine in arch.findall("machine"):
            if machine.text == "q35":
                return machine.get("canonical"), emulator
    raise RuntimeError("Destination does not offer a q35 machine type")


def ssh(ip, key, command):
    return run("ssh", "-i", str(key), *KEY_OPTIONS, "admin@" + ip, command)


def wait_for_ssh(name, key, timeout=240):
    deadline = time.monotonic() + timeout
    ip = None
    while time.monotonic() < deadline:
        out = virsh("domifaddr", name, "--source", "lease", check=False)
        found = re.findall(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)/", out)
        if found:
            ip = found[0]
            try:
                ssh(ip, key, "true")
                return ip
            except subprocess.CalledProcessError:
                pass
        time.sleep(3)
    raise RuntimeError("Guest SSH did not become reachable (last IP %s)" % ip)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", required=True, type=Path, help="Transferred recovery-set directory")
    parser.add_argument("--key", required=True, type=Path, help="Dedicated guest private key (not in the set)")
    parser.add_argument("--name", required=True, help="Exact domain name recorded in the set")
    parser.add_argument("--point", type=int, help="Recovery point number (point-based sets)")
    parser.add_argument("--single", action="store_true", help="Full-only set with a top-level management directory")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run as root on the replacement host")
    if args.single == (args.point is not None):
        raise RuntimeError("Choose either --single or --point N")
    os.environ["LC_ALL"] = "C"
    os.umask(0o077)
    recovery, name = args.set, args.name
    sums = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS"], cwd=recovery, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if sums.returncode != 0:
        raise RuntimeError("Refusing to restore: the sealed set does not match SHA256SUMS; "
                           "re-copy it from retained storage:\n" + sums.stdout.strip())
    print("SET CHECKSUMS OK", flush=True)
    if args.single:
        management, expected_file, until = recovery / "management", recovery / "management" / "guest-expected.txt", []
        config = None
    else:
        point_dir = recovery / "points" / str(args.point)
        management, expected_file = point_dir / "management", point_dir / "guest-expected.txt"
        until = ["--until", "virtnbdbackup.%d" % args.point]
        config = json.loads((point_dir / "config.json").read_text())
    backup = recovery / "backup"
    if virsh("domstate", name, check=False).strip() or Path("/var/lib/kvm-vm/state/%s.json" % name).exists():
        raise RuntimeError("Name conflict on destination: " + name)
    disk_dir, cloud_dir = Path("/var/lib/libvirt/images/vm"), Path("/var/lib/libvirt/images/cloud-init") / name
    definition_dir, state_dir = Path("/etc/kvm-vm/definitions"), Path("/var/lib/kvm-vm/state")
    disk, restored = disk_dir / (name + ".qcow2"), Path("/var/lib/libvirt/images/restored-" + name)
    for path in (disk, cloud_dir, restored, definition_dir / (name + ".yaml")):
        if path.exists():
            raise RuntimeError("Destination path conflict: " + str(path))
    # virtnbdrestore exits 0 for a missing increment or an unknown --until, so refuse first.
    run("kvm-vm", "backup", "check", str(backup), *until, capture=False)
    start = time.monotonic()
    run("virtnbdrestore", "-i", str(backup), "-o", "verify", capture=False)
    run("virtnbdrestore", "-i", str(backup), "-o", str(restored), *until, capture=False)
    restore_seconds = time.monotonic() - start
    files = sorted(restored.glob("*.qcow2"))
    if len(files) != 1:
        raise RuntimeError("Expected exactly one restored disk: %r" % files)
    info = json.loads(run("qemu-img", "info", "--output=json", str(files[0])))
    if info.get("format") != "qcow2" or "backing-filename" in info:
        raise RuntimeError("Restored disk is not a standalone qcow2")
    run("qemu-img", "check", str(files[0]))
    disk_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(files[0]), str(disk))
    shutil.copytree(management / "cloud", cloud_dir, copy_function=shutil.copy2)
    state = json.loads((management / "state.json").read_text())
    old_disk, old_cloud, old_seed = state["disk"], state["cloud_dir"], state["seed"]
    state.update(definition=str(definition_dir / (name + ".yaml")), disk=str(disk),
                 cloud_dir=str(cloud_dir), seed=str(cloud_dir / "seed.img"))
    definition_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(management / "effective.yaml", definition_dir / (name + ".yaml"))
    (state_dir / (name + ".json")).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    machine, emulator = host_machine_and_emulator()
    xml = (management / "domain.xml").read_text()
    xml = xml.replace(old_disk, str(disk)).replace(old_seed, str(cloud_dir / "seed.img"))
    xml = re.sub(r"(<type arch='x86_64' machine=')[^']+(')", lambda m: m.group(1) + machine + m.group(2), xml)
    xml = re.sub(r"<emulator>[^<]*</emulator>", "<emulator>%s</emulator>" % emulator, xml)
    for leftover, current in ((old_disk, disk), (old_cloud, cloud_dir)):
        if leftover != str(current) and leftover in xml:
            raise RuntimeError("Source path remains in the domain XML: " + leftover)
    work = recovery.parent / ("work-%s-%s" % (name, "single" if args.single else args.point))
    work.mkdir(exist_ok=True)
    (work / "domain.xml").write_text(xml, encoding="utf-8")
    virsh("define", str(work / "domain.xml"))
    defined = ET.fromstring(virsh("dumpxml", "--inactive", name))
    if config is not None:
        assert int(defined.findtext("vcpu")) == config["vcpus"], "vCPU configuration of the chosen point"
        assert int(defined.findtext("memory")) // 1024 == config["memory_mib"], "memory of the chosen point"
    boot = time.monotonic()
    virsh("start", name)
    ip = wait_for_ssh(name, args.key)
    ssh(ip, args.key, "sudo cloud-init status --wait")
    print("BOOT TO SSH %.1f s; RESTORE %.1f s" % (time.monotonic() - boot, restore_seconds), flush=True)
    command_file = (recovery / "management" if args.single else recovery) / "guest-identity-command.txt"
    command = command_file.read_text().strip()
    expected = expected_file.read_text().strip()
    actual = ssh(ip, args.key, command).strip()
    if actual != expected:
        raise AssertionError("Guest data or identity differs from the chosen point.\nexpected:\n%s\nactual:\n%s"
                             % (expected, actual))
    if config is not None:
        cpus = int(ssh(ip, args.key, "nproc").strip())
        assert cpus == config["vcpus"], "guest CPUs %d != %d" % (cpus, config["vcpus"])
    uuid_expected = ET.fromstring((management / "domain.xml").read_text()).findtext("uuid")
    assert defined.findtext("uuid") == uuid_expected
    print("CHOSEN-POINT DATA, IDENTITY AND CONFIGURATION MATCH", flush=True)
    run("kvm-vm", "status", name, capture=False)
    listing = run("kvm-vm", "list", "--managed")
    assert re.search(r"^%s\s+running\s+yes" % re.escape(name), listing, re.M), listing
    run("kvm-vm", "validate", str(definition_dir / (name + ".yaml")))
    print("RESTORE PASSED (domain left running): " + name, flush=True)


if __name__ == "__main__":
    main()
