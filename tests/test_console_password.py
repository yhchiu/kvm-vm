"""Console credentials through command handlers and real POSIX files."""
import json
import os
import stat
import subprocess

import pytest

from test_unit import kvm_vm, reinstall_machine
from test_macvtap import invoke


@pytest.fixture
def console_machine(reinstall_machine, monkeypatch):
    machine = reinstall_machine
    machine.update(failure=None, install_succeeds=True)
    definition = kvm_vm.Path(machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["cloud_init"]["console_password"] = True
    kvm_vm.yaml_dump(definition, raw)
    private = machine["root"] / "credentials"
    private.mkdir(mode=0o700)
    machine["credential"] = private / "clone01.json"
    external = kvm_vm.subprocess.run
    # Hashing is a real host process; virtualization stays simulated.
    real_process = subprocess.Popen

    def host(cmd, **kwargs):
        if cmd[0] == "openssl":
            with real_process(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as proc:
                out, err = proc.communicate(kwargs.get("input"))
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        return external(cmd, **kwargs)

    monkeypatch.setattr(kvm_vm.subprocess, "run", host)
    return machine


def test_validate_console_policy_is_explicit(console_machine, capsys):
    assert invoke("validate", console_machine["args"].definition) == 0
    data = kvm_vm.yaml.safe_load(capsys.readouterr().out)
    assert data["cloud_init"]["console_password"] is True
    assert not console_machine["credential"].exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires real POSIX ownership and permissions")
def test_create_delivers_console_credential_without_leaks(console_machine, capsys, caplog):
    machine = console_machine
    destination = machine["credential"]
    assert invoke("create", machine["args"].definition, "--no-start",
                  "--console-password-file", str(destination)) == 0
    delivered = json.loads(destination.read_text())
    assert delivered["user"] == "admin" and delivered["vm"] == "clone01"
    password = delivered["password"]
    assert len(password) >= 24
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert destination.stat().st_uid == os.geteuid()
    cloud = kvm_vm.CLOUD_DIR / "clone01"
    data = kvm_vm.yaml_load(cloud / "user-data")
    assert data["users"][0]["hashed_passwd"].startswith("$6$")
    assert data["ssh_pwauth"] is False
    assert data["disable_root"] is True
    assert data.get("runcmd")  # Local unlock is gated by guest SSH policy checks.
    for path in (cloud / "user-data", cloud / "seed.img"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for root in (kvm_vm.STATE_DIR, kvm_vm.DEF_DIR, kvm_vm.CLOUD_DIR):
        for path in root.rglob("*"):
            if path.is_file():
                assert password.encode() not in path.read_bytes(), "Plaintext leaked into managed artifacts"
    captured = capsys.readouterr()
    assert password not in captured.out + captured.err + caplog.text, "Plaintext leaked into output"


@pytest.mark.parametrize("value", ["true", 1, None])
def test_validate_rejects_non_boolean_policy(console_machine, value):
    definition = kvm_vm.Path(console_machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["cloud_init"]["console_password"] = value
    kvm_vm.yaml_dump(definition, raw)
    with pytest.raises(kvm_vm.KVMError, match="boolean"):
        invoke("validate", str(definition))


def test_console_password_refuses_root(console_machine):
    definition = kvm_vm.Path(console_machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["cloud_init"]["user"] = "root"
    kvm_vm.yaml_dump(definition, raw)
    with pytest.raises(kvm_vm.KVMError, match="non-root"):
        invoke("validate", str(definition))


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_dry_run_never_generates_secret(console_machine, monkeypatch):
    def forbidden(*args):
        pytest.fail("Preview generated a secret")
    monkeypatch.setattr(kvm_vm.secrets, "choice", forbidden)
    invoke("create", console_machine["args"].definition, "--dry-run",
           "--console-password-file", str(console_machine["credential"]))
    assert not console_machine["credential"].exists()
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("case", ["existing", "symlink", "parent_symlink", "public_directory", "missing"])
def test_unsafe_delivery_leaves_guest_untouched(console_machine, case):
    destination = console_machine["credential"]
    if case == "existing":
        destination.write_text("keep")
    elif case == "symlink":
        destination.symlink_to(destination.parent / "absent")
    elif case == "parent_symlink":
        alias = destination.parent.parent / "alias"
        alias.symlink_to(destination.parent, target_is_directory=True)
        destination = alias / destination.name
    elif case == "public_directory":
        destination.parent.chmod(0o755)
    elif case == "missing":
        destination = destination.parent / "absent" / "credential"
    with pytest.raises(kvm_vm.KVMError):
        invoke("create", console_machine["args"].definition,
               "--console-password-file", str(destination))
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    if case == "existing":
        assert destination.read_text() == "keep"


def test_create_requires_explicit_destination(console_machine):
    with pytest.raises(kvm_vm.KVMError, match="requires --console-password-file"):
        invoke("create", console_machine["args"].definition)
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()


def test_destination_cannot_enable_password(console_machine):
    definition = kvm_vm.Path(console_machine["args"].definition)
    raw = kvm_vm.yaml_load(definition)
    raw["cloud_init"]["console_password"] = False
    kvm_vm.yaml_dump(definition, raw)
    with pytest.raises(kvm_vm.KVMError, match="requires cloud_init.console_password"):
        invoke("create", str(definition), "--console-password-file", str(console_machine["credential"]))
    assert not console_machine["credential"].exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("failure", ["race", "write"])
def test_delivery_failure_precedes_guest_mutation(console_machine, monkeypatch, failure):
    destination = console_machine["credential"]
    original_open = os.open
    original_fsync = os.fsync

    def race_open(path, flags, *args, **kwargs):
        if flags & os.O_EXCL:
            destination.write_text("concurrent owner")
        return original_open(path, flags, *args, **kwargs)

    def failed_sync(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("synthetic write failure")
        return original_fsync(fd)

    monkeypatch.setattr(os, "open" if failure == "race" else "fsync",
                        race_open if failure == "race" else failed_sync)
    with pytest.raises(kvm_vm.KVMError, match="securely deliver"):
        invoke("create", console_machine["args"].definition,
               "--console-password-file", str(destination))
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    assert not console_machine.get("target_defined")
    if failure == "race":
        assert destination.read_text() == "concurrent owner"


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_late_failure_preserves_delivered_credential(console_machine):
    console_machine["install_succeeds"] = False
    destination = console_machine["credential"]
    with pytest.raises(subprocess.CalledProcessError):
        invoke("create", console_machine["args"].definition,
               "--console-password-file", str(destination))
    assert len(json.loads(destination.read_text())["password"]) == 24
    assert (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    assert console_machine["target_defined"]


@pytest.mark.parametrize("operation", ["clone_target", "clone_source", "reinstall"])
def test_unsupported_lifecycle_refuses_before_mutation(console_machine, operation):
    if operation != "clone_target":
        source = kvm_vm.DEF_DIR / "test01.yaml"
        raw = kvm_vm.yaml_load(source)
        raw["cloud_init"]["console_password"] = True
        kvm_vm.yaml_dump(source, raw)
        target = kvm_vm.Path(console_machine["args"].definition)
        raw = kvm_vm.yaml_load(target)
        raw["cloud_init"]["console_password"] = False
        kvm_vm.yaml_dump(target, raw)
    snapshots = {p: p.read_bytes() for p in console_machine["root"].rglob("*") if p.is_file()}
    args = (["reinstall", "test01", "--yes"] if operation == "reinstall" else
            ["clone", "test01", console_machine["args"].definition])
    with pytest.raises(kvm_vm.KVMError, match="not supported yet"):
        invoke(*args)
    assert console_machine["defined"]
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    for path, content in snapshots.items():
        assert path.read_bytes() == content


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("ssh_valid", [True, False])
def test_generated_guest_script_hardens_conditions_before_unlock(console_machine, monkeypatch, ssh_valid):
    invoke("create", console_machine["args"].definition, "--no-start",
           "--console-password-file", str(console_machine["credential"]))
    data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    assert data["users"][0]["lock_passwd"] is True
    script = data["runcmd"][-1][2]
    ssh_dir = console_machine["root"] / "guest-ssh"
    ssh_dir.mkdir()
    (ssh_dir / "sshd_config").write_text(
        "Include *.conf\nPasswordAuthentication yes\nMatch User admin\n"
        "  KbdInteractiveAuthentication yes\n")
    included = ssh_dir / "vendor.conf"
    included.write_text("Match Address 192.0.2.0/24\nPasswordAuthentication=yes\n"
                        "ChallengeResponseAuthentication yes\n")
    calls = []

    def guest_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd == ["/usr/sbin/sshd", "-t"] and not ssh_valid:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "loaded\n", "")

    monkeypatch.setattr(subprocess, "run", guest_run)
    monkeypatch.setattr(subprocess, "check_output",
                        lambda *args, **kwargs: "passwordauthentication no\nkbdinteractiveauthentication no\n")
    monkeypatch.setattr(kvm_vm.sys, "argv", ["-c", "admin"])
    script = script.replace("/etc/ssh", str(ssh_dir))
    if ssh_valid:
        exec(compile(script, "<generated guest provisioning>", "exec"), {})
        assert calls[-1] == ["usermod", "-U", "admin"]
        assert ["systemctl", "enable", "--now", "serial-getty@ttyS0.service"] in calls
    else:
        with pytest.raises(subprocess.CalledProcessError):
            exec(compile(script, "<generated guest provisioning>", "exec"), {})
        assert not any(cmd[0] == "usermod" for cmd in calls)
    for path in (ssh_dir / "sshd_config", included):
        assert "yes" not in path.read_text()
    assert data["users"][0]["sudo"] == "ALL=(ALL) NOPASSWD:ALL"


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_independent_creates_receive_distinct_credentials(console_machine):
    definition = kvm_vm.Path(console_machine["args"].definition)
    first = console_machine["credential"]
    invoke("create", str(definition), "--no-start", "--console-password-file", str(first))
    first_data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    console_machine["target_defined"] = False
    raw = kvm_vm.yaml_load(definition)
    raw["vm"]["name"] = "second01"
    kvm_vm.yaml_dump(definition, raw)
    second = first.with_name("second01.json")
    invoke("create", str(definition), "--no-start", "--console-password-file", str(second))
    second_data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "second01" / "user-data")
    assert json.loads(first.read_text())["password"] != json.loads(second.read_text())["password"]
    assert first_data["users"][0]["hashed_passwd"] != second_data["users"][0]["hashed_passwd"]


def test_delete_keeps_operator_credential(console_machine):
    destination = console_machine["credential"]
    destination.write_text("operator-owned credential")
    invoke("delete", "test01", "--yes")
    assert destination.read_text() == "operator-owned credential"
