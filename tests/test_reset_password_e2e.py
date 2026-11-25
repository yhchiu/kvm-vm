"""Opt-in live reset acceptance. Creates and cleans up disposable managed VMs only."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest
import yaml

from test_e2e import (E2EHelper, LIBVIRT_URI, E2E_DISTRO, E2E_SSH_TIMEOUT, build_vm_yaml,
                      pytestmark as live_marks, ssh_keypair, vm_cleanup_tracker)

pytestmark = live_marks + [pytest.mark.skipif(
    os.environ.get("KVM_VM_RESET_E2E") != "1" or LIBVIRT_URI != "qemu:///system",
    reason="Opt in with KVM_VM_RESET_E2E=1 on a disposable local KVM host")]


def console_probe(name, credential, *, reject=False):
    command = [sys.executable, str(Path(__file__).with_name("serial_auth_probe.py")),
               name, "--credential", str(credential)]
    if reject:
        command.append("--reject")
    # The probe deliberately redacts terminal output and never passes passwords in argv.
    result = subprocess.run(command, capture_output=True, text=True, timeout=150)
    assert result.returncode == 0, "Redacted serial authentication probe failed"


@pytest.mark.parametrize("console_enabled", [True, False])
def test_live_password_reset(console_enabled, tmp_path, ssh_keypair, vm_cleanup_tracker):
    pytest.importorskip("pexpect")
    name = "e2e-reset-" + uuid.uuid4().hex[:10]
    vm_cleanup_tracker.add(name)
    raw = yaml.safe_load(build_vm_yaml(name, ssh_keypair["public_key_content"], distro=E2E_DISTRO, memory_mib=2048))
    raw["cloud_init"]["console_password"] = console_enabled
    definition = tmp_path / "vm.yaml"
    definition.write_text(yaml.safe_dump(raw))
    private = tmp_path / "credentials"
    private.mkdir(mode=0o700)
    old, first, second = (private / n for n in ("initial.json", "first.json", "second.json"))
    args = ["create", "--yes", str(definition)]
    if console_enabled:
        args += ["--console-password-file", str(old)]
    E2EHelper.run_kvm_vm(*args)
    key = ssh_keypair["private_key_path"]
    ip = E2EHelper.wait_for_ssh(name, key, timeout=E2E_SSH_TIMEOUT)
    assert "done" in E2EHelper.ssh_exec(ip, key, f"sudo timeout {E2E_SSH_TIMEOUT} cloud-init status --wait", retries=1)
    if console_enabled:
        console_probe(name, old)
    else:
        denied = E2EHelper.run_kvm_vm("reset-password", name, "--password-file", str(first), "--yes", check=False)
        assert denied.returncode != 0 and not first.exists()
    flags = [] if console_enabled else ["--enable-console"]
    E2EHelper.run_kvm_vm("reset-password", name, "--password-file", str(first), "--dry-run", *flags)
    assert not first.exists()
    E2EHelper.run_kvm_vm("reset-password", name, "--password-file", str(first), "--yes", *flags)
    console_probe(name, first)
    if console_enabled:
        console_probe(name, old, reject=True)
    # Rotate again; the new password works, the previous one no longer does.
    E2EHelper.run_kvm_vm("reset-password", name, "--password-file", str(second), "--yes")
    console_probe(name, second)
    console_probe(name, first, reject=True)
    assert E2EHelper.ssh_exec(ip, key, "sudo -n id -u") == "0"
    settings = E2EHelper.ssh_exec(ip, key, "sudo /usr/sbin/sshd -T")
    assert "passwordauthentication no" in settings.splitlines()
    assert ("kbdinteractiveauthentication no" in settings.splitlines()
            or "challengeresponseauthentication no" in settings.splitlines())
    assert E2EHelper.domain_state(name) == "running"
