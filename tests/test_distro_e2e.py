"""Create a real VM and verify SSH for every declared distro alias.

Run on a Linux KVM host with:
    sudo python3 -m pytest tests/test_distro_e2e.py -v -s

KVM_VM_E2E_DISTROS selects comma-separated aliases (default: all declared aliases).
KVM_VM_E2E_SSH_TIMEOUT controls provisioning wait time in seconds (default: 600).
"""

from __future__ import annotations

import os
import re
import runpy
import uuid

import pytest
import yaml

from test_e2e import (
    E2EHelper,
    KVM_VM_BIN,
    build_vm_yaml,
    pytestmark,
    ssh_keypair,
    vm_cleanup_tracker,
)


_SUPPORTED = runpy.run_path(str(KVM_VM_BIN))["DISTROS"]
_SELECTED = os.environ.get("KVM_VM_E2E_DISTROS")
DISTROS = [item.strip() for item in _SELECTED.split(",") if item.strip()] if _SELECTED else list(_SUPPORTED)
if not DISTROS or set(DISTROS) - set(_SUPPORTED):
    raise pytest.UsageError("KVM_VM_E2E_DISTROS must select declared distro aliases")
SSH_TIMEOUT = int(os.environ.get("KVM_VM_E2E_SSH_TIMEOUT", "600"))


@pytest.mark.parametrize("distro", DISTROS)
def test_distro_create_ssh(distro, tmp_path, ssh_keypair, vm_cleanup_tracker):
    name = f"e2e-distro-{distro.replace('.', '-')}-{uuid.uuid4().hex[:6]}"
    vm_cleanup_tracker.add(name)
    port = 2222
    raw = yaml.safe_load(build_vm_yaml(name, ssh_keypair["public_key_content"], distro=distro, memory_mib=2048, ssh_port=port))
    raw["vm"]["vcpus"] = 2
    raw["storage"]["disk_gib"] = 20
    if distro.startswith(("rocky", "almalinux")):
        # Custom SSH ports need both SELinux permission and an active firewall rule.
        raw["cloud_init"]["runcmd"].append([
            "sh", "-c",
            f"if systemctl is-active --quiet firewalld; then "
            f"firewall-cmd --permanent --add-port={port}/tcp && firewall-cmd --add-port={port}/tcp; fi",
        ])
    definition = tmp_path / f"{name}.yaml"
    definition.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    print(f"CREATE_SSH_START distro={distro} vm={name} port={port}", flush=True)
    result = E2EHelper.run_kvm_vm("create", str(definition))
    assert result.returncode == 0
    assert E2EHelper.domain_exists(name)
    assert E2EHelper.domain_state(name) == "running"

    key = ssh_keypair["private_key_path"]
    ip = E2EHelper.wait_for_ssh(name, key, timeout=SSH_TIMEOUT, port=port)
    assert E2EHelper.ssh_exec(ip, key, "whoami", port=port) == "admin"
    assert E2EHelper.ssh_exec(ip, key, "hostname", port=port) == name
    status = E2EHelper.ssh_exec(ip, key, f"sudo timeout {SSH_TIMEOUT} cloud-init status --wait", port=port, retries=1)
    assert "done" in status
    release_text = E2EHelper.ssh_exec(ip, key, "cat /etc/os-release", port=port)
    release = dict(line.split("=", 1) for line in release_text.splitlines() if "=" in line)
    os_id = release["ID"].strip('"')
    version = release["VERSION_ID"].strip('"')
    expected = re.fullmatch(r"([a-z]+)([0-9.]+)", distro)
    assert os_id == expected[1]
    assert version == expected[2] or version.startswith(expected[2] + ".")
    assert E2EHelper.ssh_exec(ip, key, "sudo ss -ltnH 'sport = :22'", port=port) == ""
    selinux = E2EHelper.ssh_exec(ip, key, "getenforce", port=port) if distro.startswith(("rocky", "almalinux")) else "n/a"
    print(f"CREATE_SSH_OK distro={distro} version={version} ip={ip} port={port} cloud_init=done selinux={selinux}", flush=True)
