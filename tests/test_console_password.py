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
        machine.setdefault("commands", []).append(list(cmd))
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
@pytest.mark.parametrize("failure", ["race", "write", "partial_write"])
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

    def partial_dump(value, output):
        output.write('{"password": "')
        output.flush()
        raise OSError("synthetic partial write")

    if failure == "partial_write":
        monkeypatch.setattr(kvm_vm.json, "dump", partial_dump)
    else:
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


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_reinstall_preserves_policy_with_new_credential(console_machine, monkeypatch):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    raw["cloud_init"]["console_password"] = True
    kvm_vm.yaml_dump(source, raw)
    old_credential = console_machine["credential"].with_name("old.json")
    old_credential.write_text("old password must survive")
    monkeypatch.setenv("KVM_VM_MACVTAP", "not-the-saved-parent")
    monkeypatch.setenv("KVM_VM_NAME", "not-the-saved-name")
    assert invoke("reinstall", "test01", "--yes", "--no-start",
                  "--console-password-file", str(console_machine["credential"])) == 0
    assert console_machine["defined"]
    delivered = json.loads(console_machine["credential"].read_text())
    assert delivered["vm"] == "test01"
    assert delivered["password"] != old_credential.read_text()
    assert old_credential.read_text() == "old password must survive"
    saved = kvm_vm.yaml_load(source)
    assert saved["cloud_init"]["console_password"] is True
    assert saved["network"] == raw["network"]
    user = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "test01" / "user-data")["users"][0]
    assert user["hashed_passwd"].startswith("$6$")


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("ssh_valid", [True, False, "actual_parser", "python36"])
def test_generated_guest_script_hardens_conditions_before_unlock(console_machine, monkeypatch, ssh_valid):
    if ssh_valid == "actual_parser" and (os.geteuid() != 0 or not kvm_vm.Path("/usr/sbin/sshd").is_file()):
        pytest.skip("Actual SSH parser verification requires a Linux root host with sshd installed")
    invoke("create", console_machine["args"].definition, "--no-start",
           "--console-password-file", str(console_machine["credential"]))
    data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    assert data["users"][0]["lock_passwd"] is True
    script = data["runcmd"][-1][-2]
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
        if ssh_valid == "python36":
            assert "text" not in kwargs and "capture_output" not in kwargs
        calls.append(cmd)
        if cmd == ["/usr/sbin/sshd", "-t"] and not ssh_valid:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0, "loaded\n", "")

    monkeypatch.setattr(subprocess, "run", guest_run)
    def guest_output(*args, **kwargs):
        if ssh_valid == "python36":
            assert "text" not in kwargs
        return "passwordauthentication no\nkbdinteractiveauthentication no\n"

    monkeypatch.setattr(subprocess, "check_output", guest_output)
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
    if ssh_valid == "actual_parser":
        for address in ("127.0.0.1", "192.0.2.5", "198.51.100.5"):
            cmd = ["/usr/sbin/sshd", "-T", "-f", str(ssh_dir / "sshd_config"),
                   "-C", f"user=admin,host=localhost,addr={address}"]
            with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
                output, errors = proc.communicate()
            assert proc.returncode == 0, errors
            effective = dict(line.split(None, 1) for line in output.splitlines() if " " in line)
            assert effective["passwordauthentication"] == "no"
            assert effective["kbdinteractiveauthentication"] == "no"


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("python3_available", [True, False])
def test_console_launcher_uses_available_guest_python(console_machine, python3_available):
    invoke("create", console_machine["args"].definition, "--no-start",
           "--console-password-file", str(console_machine["credential"]))
    data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    command = list(data["runcmd"][-1])
    assert command[:2] == ["sh", "-c"]
    binaries = console_machine["root"] / "guest-bin"
    binaries.mkdir()
    interpreter = binaries / ("python3" if python3_available else "platform-python")
    interpreter.write_text("#!/bin/sh\nprintf '%s\\n' selected-interpreter \"$@\"\n")
    interpreter.chmod(0o700)
    command[2] = command[2].replace("/usr/libexec/platform-python", str(binaries / "platform-python"))
    command[-2] = "pass"
    command[0] = "/bin/sh"
    with subprocess.Popen(command, env={"PATH": str(binaries)}, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        output, errors = process.communicate()
    assert process.returncode == 0, errors
    assert output.splitlines() == ["selected-interpreter", "-c", "pass", "admin"]


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


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("source_enabled,target_enabled", [(True, True), (True, False), (False, True), (False, False)])
def test_clone_uses_independent_target_policy(console_machine, source_enabled, target_enabled):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    raw["cloud_init"]["console_password"] = source_enabled
    kvm_vm.yaml_dump(source, raw)
    target = kvm_vm.Path(console_machine["args"].definition)
    raw = kvm_vm.yaml_load(target)
    raw["cloud_init"]["console_password"] = target_enabled
    kvm_vm.yaml_dump(target, raw)
    source_cloud = kvm_vm.CLOUD_DIR / "test01" / "user-data"
    source_cloud.write_text("#cloud-config\nusers:\n- name: admin\n  lock_passwd: false\n  hashed_passwd: source-hash\n")
    preserved = {path: path.read_bytes() for path in
                 (source, source_cloud, console_machine["disk"], kvm_vm.STATE_DIR / "test01.json")}
    args = ["clone", "test01", str(target), "--no-start"]
    if target_enabled:
        args += ["--console-password-file", str(console_machine["credential"])]
    assert invoke(*args) == 0
    user_data = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "clone01" / "user-data")
    account = user_data["users"][0]
    assert account["lock_passwd"] is True
    locking = [cmd for cmd in console_machine["commands"]
               if cmd[0] == "virt-customize" and "--run-command" in cmd and "usermod -L" in cmd[-1]]
    assert locking, "Inherited password must be locked offline before the clone can boot"
    assert all(str(console_machine["disk"]) not in cmd for cmd in locking)
    if target_enabled:
        assert account["hashed_passwd"].startswith("$6$")
        assert account["hashed_passwd"] != "source-hash"
        assert len(json.loads(console_machine["credential"].read_text())["password"]) == 24
        assert user_data["runcmd"][-1][-1] == "admin"
    else:
        assert "hashed_passwd" not in account
        assert not console_machine["credential"].exists()
    for path, original in preserved.items():
        assert path.read_bytes() == original


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX ownership")
def test_foreign_private_directory_is_rejected(console_machine):
    if os.geteuid() != 0:
        pytest.skip("Ownership setup requires root")
    directory = console_machine["credential"].parent
    os.chown(directory, 65534, -1)
    try:
        with pytest.raises(kvm_vm.KVMError, match="owned by"):
            invoke("create", console_machine["args"].definition,
                   "--console-password-file", str(console_machine["credential"]))
        assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()
    finally:
        os.chown(directory, os.geteuid(), -1)


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("case", ["preview", "delivery", "late_failure", "offline_lock"])
def test_clone_failure_and_preview_preserve_source(console_machine, monkeypatch, case):
    originals = {p: p.read_bytes() for p in console_machine["root"].rglob("*") if p.is_file()}
    destination = console_machine["credential"]
    args = ["clone", "test01", console_machine["args"].definition,
            "--console-password-file", str(destination)]
    if case == "preview":
        monkeypatch.setattr(kvm_vm.secrets, "choice", lambda *args: pytest.fail("Preview generated a secret"))
        assert invoke(*args, "--dry-run") == 0
        assert not destination.exists()
    elif case == "delivery":
        destination.write_text("existing credential")
        with pytest.raises(kvm_vm.KVMError, match="already exists"):
            invoke(*args)
        assert destination.read_text() == "existing credential"
    else:
        if case == "offline_lock":
            external = kvm_vm.subprocess.run

            def fail_lock(cmd, **kwargs):
                if cmd[0] == "virt-customize" and "usermod -L" in cmd[-1]:
                    raise subprocess.CalledProcessError(1, cmd)
                return external(cmd, **kwargs)

            monkeypatch.setattr(kvm_vm.subprocess, "run", fail_lock)
        else:
            console_machine["install_succeeds"] = False
        with pytest.raises(subprocess.CalledProcessError):
            invoke(*args)
        assert (kvm_vm.VM_DIR / "clone01.qcow2").exists() == (case == "late_failure")
        if case == "offline_lock":
            assert not console_machine.get("target_defined")
        password = json.loads(destination.read_text())["password"]
        assert all(password not in " ".join(cmd) for cmd in console_machine["commands"])
        for root in (kvm_vm.STATE_DIR, kvm_vm.DEF_DIR, kvm_vm.CLOUD_DIR):
            for path in root.rglob("*"):
                if path.is_file():
                    assert password.encode() not in path.read_bytes()
    for path, content in originals.items():
        assert path.read_bytes() == content
    if case in ("preview", "delivery"):
        assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_clone_locks_previous_management_account_when_user_changes(console_machine):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    raw["cloud_init"].update(user="previous", console_password=True)
    kvm_vm.yaml_dump(source, raw)
    invoke("clone", "test01", console_machine["args"].definition, "--no-start",
           "--console-password-file", str(console_machine["credential"]))
    commands = console_machine["commands"]
    previous_lock = next(i for i, cmd in enumerate(commands) if "usermod -L previous" in cmd[-1])
    target_lock = next(i for i, cmd in enumerate(commands) if "usermod -L admin" in cmd[-1])
    install = next(i for i, cmd in enumerate(commands) if cmd[0] == "virt-install")
    assert previous_lock < install and target_lock < install


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
@pytest.mark.parametrize("case", ["preview", "missing", "existing", "symlink", "public", "write", "undefine", "late"])
def test_reinstall_credential_failure_boundaries(console_machine, monkeypatch, case):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    raw["cloud_init"]["console_password"] = True
    kvm_vm.yaml_dump(source, raw)
    originals = {p: p.read_bytes() for p in console_machine["root"].rglob("*") if p.is_file()}
    destination = console_machine["credential"]
    args = ["reinstall", "test01", "--yes", "--force", "--no-start"]
    if case != "missing":
        args += ["--console-password-file", str(destination)]
    if case == "existing":
        destination.write_text("keep existing")
    elif case == "symlink":
        destination.symlink_to(destination.parent / "absent")
    elif case == "public":
        destination.parent.chmod(0o755)
    elif case == "write":
        def failed_write(*args):
            raise OSError("synthetic write failure")
        monkeypatch.setattr(kvm_vm.json, "dump", failed_write)
    elif case == "undefine":
        console_machine["failure"] = "synthetic undefine failure"
    elif case == "late":
        console_machine["install_succeeds"] = False
    if case in ("preview", "missing", "existing", "symlink", "public", "write"):
        console_machine["power"] = "running"
    if case == "preview":
        monkeypatch.setattr(kvm_vm.secrets, "choice", lambda *args: pytest.fail("Preview generated a secret"))
        assert invoke(*args, "--dry-run") == 0
        assert not destination.exists()
    else:
        with pytest.raises((kvm_vm.KVMError, subprocess.CalledProcessError)):
            invoke(*args)
    if case != "late":
        for path, content in originals.items():
            assert path.read_bytes() == content
    if case in ("preview", "missing", "existing", "symlink", "public", "write"):
        assert not any(cmd[0] == "virsh" and cmd[3] in ("destroy", "undefine")
                       for cmd in console_machine["commands"])
    if case in ("undefine", "late"):
        password = json.loads(destination.read_text())["password"]
        assert all(password not in " ".join(cmd) for cmd in console_machine["commands"])
        for root in (kvm_vm.STATE_DIR, kvm_vm.DEF_DIR, kvm_vm.CLOUD_DIR):
            for path in root.rglob("*"):
                if path.is_file():
                    assert password.encode() not in path.read_bytes()


@pytest.mark.parametrize("legacy", [False, True])
def test_reinstall_disabled_policy_keeps_password_locked(console_machine, legacy):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    if legacy:
        raw["cloud_init"].pop("console_password", None)
    else:
        raw["cloud_init"]["console_password"] = False
    kvm_vm.yaml_dump(source, raw)
    assert invoke("reinstall", "test01", "--yes", "--no-start") == 0
    account = kvm_vm.yaml_load(kvm_vm.CLOUD_DIR / "test01" / "user-data")["users"][0]
    assert account["lock_passwd"] is True
    assert "hashed_passwd" not in account
    assert not console_machine["credential"].exists()


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX delivery")
def test_forced_reinstall_delivers_before_shutdown_with_changed_image(console_machine, monkeypatch):
    source = kvm_vm.DEF_DIR / "test01.yaml"
    raw = kvm_vm.yaml_load(source)
    raw["cloud_init"]["console_password"] = True
    kvm_vm.yaml_dump(source, raw)
    replacement = kvm_vm.BASE_DIR / "different.qcow2"
    replacement.write_bytes(b"different base")
    console_machine["power"] = "running"
    external = kvm_vm.subprocess.run
    shutdown = []

    def host(cmd, **kwargs):
        if cmd[0] == "virsh" and cmd[3] == "destroy":
            assert len(json.loads(console_machine["credential"].read_text())["password"]) == 24
            shutdown.append(cmd)
            console_machine["power"] = "shut off"
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return external(cmd, **kwargs)

    monkeypatch.setattr(kvm_vm.subprocess, "run", host)
    assert invoke("reinstall", "test01", "--force", "--yes", "--no-start",
                  "--image-path", str(replacement),
                  "--console-password-file", str(console_machine["credential"])) == 0
    assert len(shutdown) == 1
    assert kvm_vm.yaml_load(source)["storage"]["image"]["path"] == str(replacement)
