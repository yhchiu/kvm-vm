"""Read-only monitoring identity discovery; no KVM host required."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from test_unit import kvm_vm

HOST = "11111111-1111-4111-8111-111111111111"
VM_A = "22222222-2222-4222-8222-222222222222"
VM_B = "33333333-3333-4333-8333-333333333333"
SECRET = "not-for-identity-output"


@pytest.fixture
def config(tmp_path, monkeypatch):
    directory = tmp_path / "monitoring"
    monkeypatch.setenv("KVM_VM_MONITORING_DIR", str(directory))
    monkeypatch.setattr(kvm_vm, "LIBVIRT_URI", "qemu:///system")
    monkeypatch.setattr(kvm_vm, "require_commands", lambda names: None)
    return directory


def save_config(directory, **overrides):
    directory.mkdir(exist_ok=True, mode=0o700)
    state = {"host_id": HOST, "host": "kvm-tpe-01", "site": "taipei",
             "url": "https://private.example/api/v1/write", "user": SECRET, **overrides}
    file = directory / "remote.json"
    file.write_text(json.dumps(state))
    file.chmod(0o600)
    return file


def listing(monkeypatch, text="", status=0):
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, status, text, SECRET)
    monkeypatch.setattr(kvm_vm, "run", run)
    return calls


def invoke():
    args = kvm_vm.build_parser().parse_args(["identity", "--json"])
    return args.func(args)


def test_identity_returns_persisted_labels_and_sorted_domains(config, monkeypatch, capsys):
    file = save_config(config)
    before = file.read_bytes(), file.stat().st_mtime_ns
    calls = listing(monkeypatch, f"{VM_B} web02\n{VM_A} web01\n")
    assert invoke() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"schema_version": 1, "host_id": HOST,
        "host": "kvm-tpe-01", "site": "taipei", "domains": [
            {"name": "web01", "vm_id": VM_A}, {"name": "web02", "vm_id": VM_B}]}
    assert SECRET not in output.out + output.err
    assert "private.example" not in output.out
    assert calls == [(["virsh", "-c", "qemu:///system", "list", "--all", "--uuid", "--name"],
                      {"check": False, "capture": True, "timeout": 10})]
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before
    assert list(config.iterdir()) == [file]


def test_unconfigured_host_does_not_generate_uuid_or_create_files(config, monkeypatch, capsys):
    monkeypatch.setenv("KVM_VM_MONITOR_HOST_ID", HOST)
    listing(monkeypatch, f"{VM_A} web01\n")
    assert invoke() == 0
    assert json.loads(capsys.readouterr().out) == {"schema_version": 1,
        "host_id": None, "host": None, "site": None,
        "domains": [{"name": "web01", "vm_id": VM_A}]}
    assert not config.exists()


def test_empty_host_and_human_output(config, monkeypatch, capsys):
    save_config(config)
    listing(monkeypatch)
    assert invoke() == 0
    assert json.loads(capsys.readouterr().out)["domains"] == []
    args = kvm_vm.build_parser().parse_args(["identity"])
    assert args.func(args) == 0
    assert f"host_id: {HOST}" in capsys.readouterr().out


@pytest.mark.parametrize("overrides", [
    {"host_id": "hostname"}, {"host_id": None}, {"host_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
    {"host": "bad label"}, {"site": ""}, {"site": 1},
])
def test_invalid_saved_identity_fails_closed(config, monkeypatch, capsys, overrides):
    save_config(config, **overrides)
    calls = listing(monkeypatch)
    with pytest.raises(kvm_vm.KVMError, match="Invalid persisted monitoring identity"):
        invoke()
    assert not calls and not capsys.readouterr().out


@pytest.mark.parametrize("raw", [b"{", b"[]", b"{}", b"\xff", b" " * 65537,
    ('{"host_id":"'+HOST+'","host_id":"'+HOST+'","host":"h","site":"s"}').encode()])
def test_malformed_config_is_not_unconfigured(config, monkeypatch, capsys, raw):
    save_config(config).write_bytes(raw)
    calls = listing(monkeypatch)
    with pytest.raises(kvm_vm.KVMError, match="Invalid persisted monitoring identity"):
        invoke()
    assert not calls and not capsys.readouterr().out


def test_symlink_file_and_directory_are_rejected(config, tmp_path):
    config.mkdir()
    path = config / "remote.json"
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(kvm_vm.KVMError, match="Cannot read monitoring identity"):
        invoke()
    path.unlink(); config.rmdir()
    config.symlink_to(tmp_path / "other", target_is_directory=True)
    with pytest.raises(kvm_vm.KVMError, match="Refusing symlink"):
        invoke()


def test_fifo_is_rejected_without_blocking(config):
    config.mkdir()
    os.mkfifo(config / "remote.json")
    with pytest.raises(kvm_vm.KVMError, match="regular file"):
        invoke()


def test_permission_failure_is_not_unconfigured(config, monkeypatch):
    save_config(config)
    def denied(*args, **kwargs):
        raise PermissionError(13, "permission denied")
    monkeypatch.setattr(kvm_vm.os, "open", denied)
    with pytest.raises(kvm_vm.KVMError, match="Cannot read monitoring identity"):
        invoke()


@pytest.mark.parametrize("uri", ["qemu+ssh://host/system", "qemu:///session", "test:///default"])
def test_other_libvirt_connections_are_not_paired_with_local_host(config, monkeypatch, uri):
    save_config(config)
    monkeypatch.setattr(kvm_vm, "LIBVIRT_URI", uri)
    calls = listing(monkeypatch)
    with pytest.raises(kvm_vm.KVMError, match="requires local qemu:///system"):
        invoke()
    assert not calls


@pytest.mark.parametrize("text,status", [(f"{VM_A} web01\n",1), ("bad-uuid web01",0),
    (f"{VM_A} vm1\n{VM_A} vm2\n",0)])
def test_failed_domain_listing_never_returns_partial_json(config, monkeypatch, capsys, text, status):
    save_config(config)
    listing(monkeypatch,text,status)
    with pytest.raises(kvm_vm.KVMError):
        invoke()
    assert not capsys.readouterr().out


def test_timeout_returns_error(config, monkeypatch):
    def timeout(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs['timeout'])
    monkeypatch.setattr(kvm_vm, "run", timeout)
    with pytest.raises(kvm_vm.KVMError, match="timed out"):
        invoke()


def test_real_cli_with_stub_virsh(config, tmp_path):
    # Exercise main(), environment, command arguments and stdout/stderr contract.
    path = save_config(config)
    stub = tmp_path / "virsh"
    stub.write_text(f'#!/bin/sh\n[ "$*" = "-c qemu:///system list --all --uuid --name" ] || exit 2\nprintf "%s\\n" "{VM_A} web01"\n')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": str(tmp_path)+os.pathsep+os.environ["PATH"],
           "KVM_VM_LIBVIRT_URI": "qemu:///system"}
    command = [sys.executable, str(Path(__file__).resolve().parents[1]/"kvm-vm"), "identity", "--json"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["domains"] == [{"name":"web01","vm_id":VM_A}]
    path.write_text("{"+SECRET)
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 1 and not result.stdout and SECRET not in result.stderr
