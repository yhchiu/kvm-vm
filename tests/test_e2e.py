"""Real-machine End-to-End (E2E) tests for kvm-vm.

These tests execute real VM lifecycle operations against libvirt/KVM on a Linux host:
- Pre-flight dry-run checks
- VM creation with cloud image download and cloud-init seed generation
- Guest OS booting and IP discovery via DHCP/libvirt lease
- SSH authentication and in-guest verification (hostname, user, cloud-init)
- VM status and list inspection
- VM cloning and sysprep verification
- VM reinstallation preserving existing configuration
- VM deletion and cleanup verification (disks, cloud-init seed, state JSON, definition)

Prerequisites:
- Linux host with hardware virtualization (/dev/kvm)
- root privileges (UID 0)
- Running libvirt daemon (qemu:///system) with active 'default' NAT network
- Required CLI tools: virsh, virt-install, qemu-img, virt-sysprep, cloud seed generator, ssh

Run with:
    sudo bash tests/run_e2e.sh
or:
    sudo python3 -m pytest tests/test_e2e.py -m e2e -v -s
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
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest
import yaml

# Locate kvm-vm script
_ROOT = Path(__file__).resolve().parent.parent
KVM_VM_BIN = _ROOT / "kvm-vm"
LIBVIRT_URI = os.environ.get("KVM_VM_LIBVIRT_URI", "qemu:///system")
E2E_DISTRO = os.environ.get("KVM_VM_E2E_DISTRO", "debian12")


def _check_e2e_prerequisites() -> Tuple[bool, str]:
    if sys.platform != "linux":
        return False, f"E2E tests require Linux host (detected: {sys.platform})"
    if os.geteuid() != 0:
        return False, "E2E tests require root privileges (UID 0)"
    if not os.path.exists("/dev/kvm"):
        return False, "/dev/kvm does not exist (hardware virtualization required)"
    for cmd in ("virsh", "virt-install", "qemu-img", "ssh", "ssh-keygen"):
        if not shutil.which(cmd):
            return False, f"Required command not found in PATH: {cmd}"
    # Verify libvirt daemon connection
    res = subprocess.run(
        ["virsh", "-c", LIBVIRT_URI, "uri"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return False, f"Cannot connect to libvirt ({LIBVIRT_URI}): {res.stderr.strip()}"
    return True, ""


_e2e_ready, _skip_reason = _check_e2e_prerequisites()
if not _e2e_ready:
    pytestmark = [pytest.mark.e2e, pytest.mark.skip(reason=_skip_reason)]
else:
    pytestmark = [pytest.mark.e2e]


class E2EHelper:
    """Helper utilities for executing kvm-vm CLI commands and interacting with test VMs."""

    @staticmethod
    def run_kvm_vm(
        *args: str,
        check: bool = True,
        capture: bool = True,
        env_extra: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["LC_ALL"] = "C"
        if env_extra:
            env.update(env_extra)
        cmd = [sys.executable, str(KVM_VM_BIN), *args]
        if capture:
            return subprocess.run(cmd, check=check, text=True, capture_output=True, env=env)
        return subprocess.run(cmd, check=check, text=True, env=env)

    @staticmethod
    def virsh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        cmd = ["virsh", "-c", LIBVIRT_URI, *args]
        return subprocess.run(cmd, check=check, text=True, capture_output=True)

    @classmethod
    def domain_exists(cls, name: str) -> bool:
        p = cls.virsh("dominfo", name, check=False)
        return p.returncode == 0

    @classmethod
    def domain_state(cls, name: str) -> str:
        p = cls.virsh("domstate", name, check=False)
        return p.stdout.strip() if p.returncode == 0 else "unknown"

    @classmethod
    def wait_domain_state(cls, name: str, expected: str, timeout: int = 60, interval: float = 2.0) -> None:
        start = time.time()
        while time.time() - start < timeout:
            if cls.domain_state(name) == expected:
                return
            time.sleep(interval)
        raise TimeoutError(f"Domain '{name}' did not reach state '{expected}' within {timeout}s (current: {cls.domain_state(name)})")

    @classmethod
    def get_vm_ips(cls, name: str) -> List[str]:
        found: List[str] = []
        seen: Set[str] = set()
        for src in ("agent", "lease", "arp"):
            p = cls.virsh("domifaddr", name, "--source", src, "--full", check=False)
            if p.returncode != 0:
                continue
            for line in p.stdout.splitlines():
                cols = line.split()
                if len(cols) >= 4 and ("ipv4" in cols or "ipv6" in cols):
                    raw_addr = cols[-1]
                    ip = raw_addr.split("/")[0].strip()
                    if "." in ip and not ip.startswith("127.") and ip not in seen:
                        seen.add(ip)
                        found.append(ip)
        return found

    @classmethod
    def wait_for_ip(cls, name: str, timeout: int = 180, interval: float = 3.0) -> str:
        start = time.time()
        while time.time() - start < timeout:
            ips = cls.get_vm_ips(name)
            if ips:
                return ips[-1]
            time.sleep(interval)
        raise TimeoutError(f"Timed out waiting for IPv4 address on VM '{name}' after {timeout}s")

    @classmethod
    def wait_for_ssh(
        cls,
        target: str,
        key_path: Path,
        user: str = "admin",
        timeout: int = 180,
        interval: float = 3.0,
    ) -> str:
        """Wait for SSH connection to succeed.

        'target' can be either an IP address or a VM domain name.
        If 'target' is a domain name, candidates are dynamically retrieved via
        get_vm_ips(target) on each poll so IP reassignments/leases are automatically tracked.

        Returns the responsive IP address.
        """
        start = time.time()
        last_stderr = ""
        while time.time() - start < timeout:
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", target):
                candidates = [target]
            else:
                candidates = list(reversed(cls.get_vm_ips(target)))

            for ip in candidates:
                cmd = [
                    "ssh",
                    "-i", str(key_path),
                    "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "UserKnownHostsFile=/dev/null",
                    "-o", "ConnectTimeout=4",
                    f"{user}@{ip}",
                    "echo __ssh_probe_ok__",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0 and "__ssh_probe_ok__" in res.stdout:
                    # Allow a brief moment for cloud-init sshd restart to settle
                    time.sleep(3.0)
                    return ip
                last_stderr = res.stderr.strip()
            time.sleep(interval)
        raise TimeoutError(f"Timed out waiting for SSH to {user}@{target} after {timeout}s. Last error: {last_stderr}")

    @staticmethod
    def ssh_exec(
        ip: str,
        key_path: Path,
        remote_cmd: str,
        user: str = "admin",
        retries: int = 6,
        retry_delay: float = 2.0,
    ) -> str:
        cmd = [
            "ssh",
            "-i", str(key_path),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "ConnectTimeout=10",
            f"{user}@{ip}",
            remote_cmd,
        ]
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                return res.stdout.strip()
            except subprocess.CalledProcessError as exc:
                last_exc = exc
                if attempt < retries - 1:
                    time.sleep(retry_delay)
        if last_exc:
            raise last_exc
        raise RuntimeError(f"ssh_exec failed on {user}@{ip}")


@pytest.fixture(scope="session")
def ssh_keypair(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Any]:
    """Generate a dedicated Ed25519 SSH keypair for the E2E test session."""
    key_dir = tmp_path_factory.mktemp("e2e-ssh")
    priv_key = key_dir / "id_ed25519"
    pub_key = key_dir / "id_ed25519.pub"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv_key), "-C", "kvm-vm-e2e"],
        check=True,
        capture_output=True,
    )
    pub_content = pub_key.read_text(encoding="utf-8").strip()
    return {
        "private_key_path": priv_key,
        "public_key_path": pub_key,
        "public_key_content": pub_content,
    }


@pytest.fixture
def vm_cleanup_tracker():
    """Tracks created VMs and guarantees teardown cleanup even upon test failure."""
    tracked_vms: Set[str] = set()

    yield tracked_vms

    for name in list(tracked_vms):
        # Best effort cleanup via kvm-vm delete
        try:
            E2EHelper.run_kvm_vm("delete", name, "--force", "--yes", check=False)
        except Exception:
            pass
        # Fallback to direct virsh cleanup if still present
        if E2EHelper.domain_exists(name):
            try:
                E2EHelper.virsh("destroy", name, check=False)
                E2EHelper.virsh("undefine", name, "--nvram", check=False)
                E2EHelper.virsh("undefine", name, check=False)
            except Exception:
                pass
        # Clean lingering managed files
        for p in (
            Path(f"/var/lib/libvirt/images/vm/{name}.qcow2"),
            Path(f"/var/lib/kvm-vm/state/{name}.json"),
            Path(f"/etc/kvm-vm/definitions/{name}.yaml"),
        ):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        cloud_dir = Path(f"/var/lib/libvirt/images/cloud-init/{name}")
        if cloud_dir.exists():
            try:
                shutil.rmtree(cloud_dir)
            except Exception:
                pass


def build_vm_yaml(name: str, pub_key: str, distro: str = E2E_DISTRO, memory_mib: int = 1024) -> str:
    return yaml.safe_dump(
        {
            "version": 1,
            "vm": {
                "name": name,
                "vcpus": 1,
                "memory_mib": memory_mib,
            },
            "storage": {
                "disk_gib": 5,
                "image": {
                    "distro": distro,
                },
            },
            "network": {
                "mode": "nat",
                "libvirt_network": "default",
                "ipv4": {
                    "method": "dhcp",
                },
            },
            "cloud_init": {
                "user": "admin",
                "ssh_authorized_keys": [pub_key],
            },
        },
        sort_keys=False,
    )


def test_e2e_dry_run_checks(tmp_path: Path, ssh_keypair: Dict[str, Any]):
    """Verify that --dry-run operates safely on a real host without altering libvirt."""
    test_name = f"e2e-dryrun-{uuid.uuid4().hex[:6]}"
    yaml_file = tmp_path / f"{test_name}.yaml"
    yaml_file.write_text(build_vm_yaml(test_name, ssh_keypair["public_key_content"]), encoding="utf-8")

    # Validate command
    res_val = E2EHelper.run_kvm_vm("validate", str(yaml_file))
    assert res_val.returncode == 0
    assert "mode: nat" in res_val.stdout

    # Create --dry-run
    res_dry = E2EHelper.run_kvm_vm("create", str(yaml_file), "--dry-run")
    assert res_dry.returncode == 0
    assert "dry-run: all pre-flight checks passed" in res_dry.stdout
    assert not E2EHelper.domain_exists(test_name)


def test_e2e_delete_keep_storage(tmp_path: Path, ssh_keypair: Dict[str, Any], vm_cleanup_tracker: Set[str]):
    """Verify that delete --keep-storage preserves disks and state file while undefining domain."""
    test_name = f"e2e-keep-{uuid.uuid4().hex[:6]}"
    vm_cleanup_tracker.add(test_name)

    yaml_file = tmp_path / f"{test_name}.yaml"
    yaml_file.write_text(build_vm_yaml(test_name, ssh_keypair["public_key_content"]), encoding="utf-8")

    # Create with --no-start for instant execution
    res_create = E2EHelper.run_kvm_vm("create", str(yaml_file), "--no-start")
    assert res_create.returncode == 0
    assert E2EHelper.domain_exists(test_name)

    expected_disk = Path(f"/var/lib/libvirt/images/vm/{test_name}.qcow2")
    expected_state = Path(f"/var/lib/kvm-vm/state/{test_name}.json")
    assert expected_disk.exists()
    assert expected_state.exists()

    # Delete with --keep-storage
    res_del = E2EHelper.run_kvm_vm("delete", test_name, "--keep-storage", "--yes")
    assert res_del.returncode == 0
    assert "storage kept" in res_del.stdout.lower()

    # Domain must be gone from libvirt
    assert not E2EHelper.domain_exists(test_name)
    # Storage and state must still exist
    assert expected_disk.exists()
    assert expected_state.exists()

    # Clean up preserved files manually
    expected_disk.unlink()
    expected_state.unlink()
    cloud_dir = Path(f"/var/lib/libvirt/images/cloud-init/{test_name}")
    if cloud_dir.exists():
        shutil.rmtree(cloud_dir)
    def_file = Path(f"/etc/kvm-vm/definitions/{test_name}.yaml")
    if def_file.exists():
        def_file.unlink()


def test_e2e_primary_lifecycle(tmp_path: Path, ssh_keypair: Dict[str, Any], vm_cleanup_tracker: Set[str]):
    """Complete real-machine lifecycle test:

    1. create: provision VM with cloud-init
    2. boot & network: acquire DHCP IP
    3. ssh verification: in-guest hostname & user checks
    4. status & list: verify CLI metadata output
    5. clone: shut off source VM, clone to target with sysprep, verify clone SSH & hostname
    6. reinstall: reinstall source VM OS with --force --yes, verify reboot & SSH
    7. delete: delete VMs with --force --yes and verify complete filesystem cleanup
    """
    src_name = f"e2e-src-{uuid.uuid4().hex[:6]}"
    clone_name = f"e2e-cln-{uuid.uuid4().hex[:6]}"
    vm_cleanup_tracker.add(src_name)
    vm_cleanup_tracker.add(clone_name)

    key_priv = ssh_keypair["private_key_path"]
    key_pub_str = ssh_keypair["public_key_content"]

    # ---------------------------------------------------------
    # Step 1: Create source VM
    # ---------------------------------------------------------
    src_yaml = tmp_path / f"{src_name}.yaml"
    src_yaml.write_text(build_vm_yaml(src_name, key_pub_str, distro=E2E_DISTRO), encoding="utf-8")

    res_create = E2EHelper.run_kvm_vm("create", str(src_yaml))
    assert res_create.returncode == 0
    assert E2EHelper.domain_exists(src_name)
    assert E2EHelper.domain_state(src_name) == "running"

    # ---------------------------------------------------------
    # Step 2: Acquire IP & SSH in-guest verification
    # ---------------------------------------------------------
    src_ip = E2EHelper.wait_for_ssh(src_name, key_priv, user="admin", timeout=180)
    assert src_ip

    # In-guest assertion: verify cloud-init applied hostname and created user
    in_guest_hostname = E2EHelper.ssh_exec(src_ip, key_priv, "hostname", user="admin")
    assert in_guest_hostname == src_name

    in_guest_user = E2EHelper.ssh_exec(src_ip, key_priv, "whoami", user="admin")
    assert in_guest_user == "admin"

    # ---------------------------------------------------------
    # Step 3: Verify status & list CLI commands
    # ---------------------------------------------------------
    res_status = E2EHelper.run_kvm_vm("status", src_name)
    assert res_status.returncode == 0
    assert "Domain" in res_status.stdout
    assert "kvm-vm state" in res_status.stdout
    assert src_ip in res_status.stdout

    res_list = E2EHelper.run_kvm_vm("list", "--managed", "--ips")
    assert res_list.returncode == 0
    assert src_name in res_list.stdout
    assert "running" in res_list.stdout

    # ---------------------------------------------------------
    # Step 4: Clone source VM (requires shut off source)
    # ---------------------------------------------------------
    # Shut down source VM cleanly
    E2EHelper.virsh("shutdown", src_name)
    E2EHelper.wait_domain_state(src_name, "shut off", timeout=60)

    # Prepare clone YAML
    clone_yaml = tmp_path / f"{clone_name}.yaml"
    clone_yaml_content = yaml.safe_dump(
        {
            "version": 1,
            "vm": {
                "name": clone_name,
                "vcpus": 1,
                "memory_mib": 1024,
            },
            "storage": {
                "disk_gib": 5,
            },
            "network": {
                "mode": "nat",
                "libvirt_network": "default",
                "ipv4": {
                    "method": "dhcp",
                },
            },
            "cloud_init": {
                "user": "admin",
                "ssh_authorized_keys": [key_pub_str],
            },
        },
        sort_keys=False,
    )
    clone_yaml.write_text(clone_yaml_content, encoding="utf-8")

    res_clone = E2EHelper.run_kvm_vm("clone", src_name, str(clone_yaml))
    assert res_clone.returncode == 0
    assert E2EHelper.domain_exists(clone_name)
    assert E2EHelper.domain_state(clone_name) == "running"

    # Verify clone acquires its own IP and responds via SSH
    clone_ip = E2EHelper.wait_for_ssh(clone_name, key_priv, user="admin", timeout=180)
    assert clone_ip

    # In-guest assertion: hostname of clone VM must be clone_name
    clone_in_guest_hostname = E2EHelper.ssh_exec(clone_ip, key_priv, "hostname", user="admin")
    assert clone_in_guest_hostname == clone_name

    # Delete clone VM now
    res_del_clone = E2EHelper.run_kvm_vm("delete", clone_name, "--force", "--yes")
    assert res_del_clone.returncode == 0
    assert not E2EHelper.domain_exists(clone_name)

    # ---------------------------------------------------------
    # Step 5: Start and then Reinstall source VM
    # ---------------------------------------------------------
    # Power on source VM first so we test reinstall with a running VM and --force
    E2EHelper.virsh("start", src_name)
    E2EHelper.wait_domain_state(src_name, "running", timeout=60)

    res_reinstall = E2EHelper.run_kvm_vm("reinstall", src_name, "--force", "--yes")
    assert res_reinstall.returncode == 0
    assert E2EHelper.domain_exists(src_name)
    assert E2EHelper.domain_state(src_name) == "running"

    # Verify reinstalled VM obtains IP and responds via SSH
    reinstalled_ip = E2EHelper.wait_for_ssh(src_name, key_priv, user="admin", timeout=180)
    assert reinstalled_ip

    reinstalled_hostname = E2EHelper.ssh_exec(reinstalled_ip, key_priv, "hostname", user="admin")
    assert reinstalled_hostname == src_name

    # ---------------------------------------------------------
    # Step 6: Delete source VM & verify total cleanup
    # ---------------------------------------------------------
    res_del_src = E2EHelper.run_kvm_vm("delete", src_name, "--force", "--yes")
    assert res_del_src.returncode == 0
    assert not E2EHelper.domain_exists(src_name)

    # Verify managed files are deleted
    assert not Path(f"/var/lib/libvirt/images/vm/{src_name}.qcow2").exists()
    assert not Path(f"/var/lib/libvirt/images/cloud-init/{src_name}").exists()
    assert not Path(f"/var/lib/kvm-vm/state/{src_name}.json").exists()
    assert not Path(f"/etc/kvm-vm/definitions/{src_name}.yaml").exists()
