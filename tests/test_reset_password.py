"""Password reset contract: real secret delivery and isolated virsh stdin."""
import json
import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import Mock

import pytest

from test_maintenance import kvm, live_vm  # same module instance and managed fixture

UUID = "f380900d-e9ce-4c5a-aad8-68136a224d05"
HASH = "$6$salt$" + "a" * 86
CAPABILITY = {"name": "guest-set-user-password", "enabled": True, "success-response": True}


def cli(*argv):
    args = kvm.build_parser().parse_args(["reset-password", *argv])
    return args.func(args)


@pytest.fixture
def reset_vm(live_vm, tmp_path, monkeypatch):
    path, cfg = live_vm
    cfg["cloud_init"]["console_password"] = True
    kvm.yaml_dump(path, cfg)
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    dest = directory / "new.json"
    monkeypatch.setattr(kvm, "output", lambda cmd: UUID if "domuuid" in cmd else "test01")
    agent = Mock(return_value={"supported_commands": [CAPABILITY]})
    guest = Mock(return_value="")
    backend = Mock()
    monkeypatch.setattr(kvm, "maintenance_agent_call", agent)
    monkeypatch.setattr(kvm, "maintenance_guest_exec", guest)
    monkeypatch.setattr(kvm, "virsh_reset_password", backend)
    return path, cfg, dest, agent, guest, backend


def invoke(reset_vm, *args):
    return cli("test01", "--password-file", str(reset_vm[2]), *args)


def test_success_delivers_before_reset(reset_vm, capsys, caplog):
    path, cfg, dest, agent, guest, backend = reset_vm
    original = path.read_bytes()
    def check(domain, name, user, password_hash, timeout):
        data = json.loads(dest.read_text())
        assert data["vm"] == name == "test01" and data["user"] == user == "admin"
        assert domain == UUID and timeout == 21 and password_hash.startswith("$6$")
        assert len(data["password"]) == 24
        assert stat.S_IMODE(dest.stat().st_mode) == 0o600
    backend.side_effect = check
    assert invoke(reset_vm, "--yes", "--timeout", "21") == 0
    assert backend.call_count == 1 and agent.call_count == 2 and guest.call_count == 2
    assert path.read_bytes() == original
    secret = json.loads(dest.read_text())["password"]
    output = capsys.readouterr()
    assert secret not in output.out + output.err + caplog.text
    assert backend.call_args.args[3] not in output.out + output.err + caplog.text
    assert not (kvm.STATE_DIR / ".lifecycle-lock").exists()


def test_enable_console_is_explicit_and_persistent(reset_vm):
    path, cfg, dest, _, guest, backend = reset_vm
    cfg["cloud_init"]["console_password"] = False
    kvm.yaml_dump(path, cfg)
    with pytest.raises(kvm.KVMError, match="--enable-console"):
        invoke(reset_vm, "--yes")
    assert not dest.exists()
    assert invoke(reset_vm, "--enable-console", "--yes") == 0
    assert kvm.yaml_load(path)["cloud_init"]["console_password"] is True
    assert kvm.yaml_load(path)["cloud_init"]["user"] == cfg["cloud_init"]["user"]
    assert any("systemctl enable --now serial-getty" in call.args[1] for call in guest.call_args_list)
    assert backend.call_count == 1


@pytest.mark.parametrize("reason", ["dry-run", "noninteractive", "cancel", "eof", "existing", "symlink", "public", "guest", "qga", "paused", "mac", "job", "snapshot", "root", "invalid-timeout", "changed-uuid"])
def test_preflight_never_changes_guest(reset_vm, monkeypatch, reason):
    path, cfg, dest, agent, guest, backend = reset_vm
    args = ["--yes"]
    if reason == "dry-run":
        monkeypatch.setattr(kvm.secrets, "choice", lambda *a: pytest.fail("generated secret during dry run"))
        assert invoke(reset_vm, "--dry-run") == 0
    else:
        if reason in ("noninteractive", "cancel", "eof"):
            args = []
            monkeypatch.setattr(kvm.sys.stdin, "isatty", lambda: reason != "noninteractive")
            def answer(*a):
                if reason == "eof":
                    raise EOFError
                return ""
            monkeypatch.setattr("builtins.input", answer)
        elif reason == "existing":
            dest.write_text("preserve")
        elif reason == "symlink":
            dest.symlink_to(dest.parent / "missing")
        elif reason == "public":
            dest.parent.chmod(0o755)
        elif reason == "guest":
            guest.side_effect = kvm.KVMError("SSH policy unknown")
        elif reason == "qga":
            agent.return_value = {"supported_commands": []}
        elif reason == "paused":
            monkeypatch.setattr(kvm, "domain_state", lambda name: "paused")
        elif reason == "mac":
            monkeypatch.setattr(kvm, "domain_macs", lambda name: set())
        elif reason == "job":
            monkeypatch.setattr(kvm, "check_domain_job", Mock(side_effect=kvm.KVMError("busy")))
        elif reason == "snapshot":
            monkeypatch.setattr(kvm, "load_snapshot_record", lambda name: {"status": "rolling-back"})
        elif reason == "root":
            cfg["cloud_init"]["user"] = "root"
            kvm.yaml_dump(path, cfg)
        elif reason == "invalid-timeout":
            args += ["--timeout", "0"]
        elif reason == "changed-uuid":
            monkeypatch.setattr(kvm, "password_reset_target", Mock(side_effect=[UUID, "new"]))
        with pytest.raises(kvm.KVMError):
            invoke(reset_vm, *args)
    backend.assert_not_called()
    assert all(not call.kwargs.get("mutate") for call in guest.call_args_list)
    if reason == "existing":
        assert dest.read_text() == "preserve"
    else:
        assert not dest.exists()
    assert not (kvm.STATE_DIR / ".lifecycle-lock").exists()


@pytest.mark.parametrize("stage", ["backend", "save", "console"])
def test_ambiguous_failure_retains_credential(reset_vm, monkeypatch, stage):
    path, cfg, dest, _, guest, backend = reset_vm
    cfg["cloud_init"]["console_password"] = False
    kvm.yaml_dump(path, cfg)
    original = path.read_bytes()
    if stage == "backend":
        backend.side_effect = kvm.KVMError("uncertain")
    elif stage == "save":
        monkeypatch.setattr(kvm, "save_effective_definition", Mock(side_effect=OSError("disk full")))
    else:
        guest.side_effect = ["", "", kvm.KVMError("failed")]
    with pytest.raises(kvm.KVMError):
        invoke(reset_vm, "--yes", "--enable-console")
    assert dest.is_file() and path.read_bytes() == original
    assert backend.call_count == (0 if stage == "console" else 1)


@pytest.mark.parametrize("response", [{}, {"supported_commands": {}}, {"supported_commands": [dict(CAPABILITY, enabled=False)]}, {"supported_commands": [dict(CAPABILITY, **{"success-response": False})]}])
def test_capability_required(monkeypatch, response):
    monkeypatch.setattr(kvm, "maintenance_agent_call", Mock(return_value=response))
    with pytest.raises(kvm.KVMError, match="does not advertise"):
        kvm.password_agent_check(UUID)


@pytest.mark.parametrize("case", ["success", "uuid-success", "prompt-success", "silent", "exit-zero-error", "nonzero", "stderr", "timeout", "interrupt", "oserror"])
def test_virsh_stdin_and_redaction(monkeypatch, caplog, capsys, case):
    monkeypatch.setenv("VIRSH_LOG_FILE", "/tmp/unsafe")
    monkeypatch.setenv("VIRSH_DEBUG", "0")
    monkeypatch.setenv("LIBVIRT_LOG_OUTPUTS", "1:file:/tmp/unsafe")
    def fake(argv, **kwargs):
        assert argv == ["virsh", "-c", kvm.LIBVIRT_URI]
        assert HASH not in str(argv) and HASH not in str(kwargs["env"])
        assert kwargs["input"] == f"set-user-password {UUID} 'admin' '{HASH}' --encrypted\n"
        assert kwargs["env"]["VIRSH_HISTSIZE"] == "0"
        assert "VIRSH_LOG_FILE" not in kwargs["env"] and "VIRSH_DEBUG" not in kwargs["env"]
        assert stat.S_IMODE(Path(kwargs["env"]["XDG_CACHE_HOME"]).stat().st_mode) == 0o700
        assert kwargs["timeout"] == 12
        if case == "timeout":
            raise subprocess.TimeoutExpired(argv, 12, output=HASH, stderr=HASH)
        if case == "interrupt":
            raise KeyboardInterrupt
        if case == "oserror":
            raise OSError(HASH)
        target = UUID if case == "uuid-success" else "test01"
        stdout = f"Password set successfully for admin in {target}\n"
        if case == "prompt-success":
            stdout = f"virsh # {kwargs['input']}" + stdout
        if case in ("silent", "exit-zero-error"):
            stdout = HASH
        stderr = HASH if case in ("stderr", "exit-zero-error") else ""
        return subprocess.CompletedProcess(argv, int(case == "nonzero"), stdout, stderr)
    monkeypatch.setattr(kvm.subprocess, "run", fake)
    if case in ("success", "uuid-success", "prompt-success"):
        kvm.virsh_reset_password(UUID, "test01", "admin", HASH, 12)
    else:
        with pytest.raises(kvm.KVMError) as exc:
            kvm.virsh_reset_password(UUID, "test01", "admin", HASH, 12)
        assert HASH not in str(exc.value)
    output = capsys.readouterr()
    assert HASH not in output.out + output.err + caplog.text


def test_virsh_real_pipe_fake_executable(tmp_path, monkeypatch):
    fake = tmp_path / "virsh"
    fake.write_text("#!/usr/bin/env python3\nimport os, sys\nassert not sys.stdin.isatty()\nassert len(sys.argv) == 3\nassert os.environ['VIRSH_HISTSIZE'] == '0'\nline = sys.stdin.readline()\nassert line.endswith(' --encrypted\\n')\nprint(line.strip())\nprint('Password set successfully for admin in test01')\n")
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    kvm.virsh_reset_password(UUID, "test01", "admin", HASH, 12)


@pytest.mark.parametrize("case", ["safe", "match-password", "include-kbd", "legacy-kbd", "effective-password", "expired", "root-alias", "missing-account"])
def test_guest_login_policy_script(tmp_path, case):
    import sys
    root = tmp_path / "ssh"
    root.mkdir()
    (root / "sshd_config").write_text("Include conf.d/*.conf\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n")
    includes = root / "conf.d"
    includes.mkdir()
    (includes / "policy.conf").write_text("# harmless comment\n")
    if case == "match-password":
        (root / "sshd_config").write_text((root / "sshd_config").read_text() + "Match User admin\nPasswordAuthentication yes\n")
    if case == "include-kbd":
        (includes / "policy.conf").write_text("ChallengeResponseAuthentication=yes\n")
    password = "$6$DO_NOT_PRINT$" + "x" * 86
    (tmp_path / "passwd").write_text("admin:x:1000:1000::/home/admin:/bin/bash\n" if case != "missing-account" else "")
    (tmp_path / "shadow").write_text(f"admin:{password}:20000:0:99999:7::{1 if case == 'expired' else ''}:\n")
    sshd = tmp_path / "sshd"
    kbd = "challengeresponseauthentication" if case == "legacy-kbd" else "kbdinteractiveauthentication"
    sshd.write_text(f"#!/bin/sh\nif [ \"$1\" = -T ]; then printf '%s\\n' 'passwordauthentication {'yes' if case == 'effective-password' else 'no'}' '{kbd} no'; fi\n")
    sshd.chmod(0o700)
    script = (kvm.PASSWORD_LOGIN_CHECK.replace("/etc/passwd", str(tmp_path / "passwd"))
              .replace("/etc/shadow", str(tmp_path / "shadow"))
              .replace("/etc/ssh/", str(root) + "/").replace("/usr/sbin/sshd", str(sshd)))
    code = ("import pwd, types, sys\n"
            f"pwd.getpwnam = lambda name: types.SimpleNamespace(pw_uid={0 if case == 'root-alias' else 1000}, pw_shell='/bin/bash')\n"
            "sys.argv = ['check', 'admin']\n" + script)
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert (result.returncode == 0) == (case in ("safe", "legacy-kbd")), result.stderr
    assert password not in result.stdout + result.stderr
