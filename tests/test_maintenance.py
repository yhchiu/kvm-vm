"""Maintenance policy, calendar generation and guest-side application tests."""
import copy
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import patch

import pytest

_loader = importlib.machinery.SourceFileLoader("maintenance_kvm_vm", str(Path(__file__).resolve().parents[1] / "kvm-vm"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
kvm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kvm)


def raw(policy=None, *, name="test01", mac="52:54:00:11:22:33"):
    result = {"vm": {"name": name}, "storage": {"image": {"distro": "debian13"}},
              "network": {"mac": mac}, "cloud_init": {"ssh_authorized_keys": ["ssh-ed25519 AAAA test"]}}
    if policy is not None:
        result["cloud_init"]["maintenance"] = policy
    return result


def cfg(policy=None, **kwargs):
    return kvm.normalize_definition(raw(policy, **kwargs))


def test_default_and_disabled():
    for policy in [None, {"profile": "none"}, {"profile": "balanced", "enabled": False}]:
        result = cfg(policy)
        assert not result["cloud_init"]["maintenance"]["enabled"]
        assert kvm.maintenance_schedules(result) == {}


def test_profiles_override_and_roundtrip():
    policy = {"profile": "balanced", "timers": {"fstrim.timer": {"window": "02:00-03:00"},
              "man-db.timer": {"enabled": False}, "backup@db.timer": {"frequency": "weekly", "window": "03:00-04:00"}}}
    result = cfg(policy)
    effective = result["cloud_init"]["maintenance"]
    assert effective == kvm.normalize_maintenance(effective)
    assert effective["timers"]["fstrim.timer"]["frequency"] == "weekly"
    assert effective["timers"]["fstrim.timer"]["window"] == "02:00-03:00"
    assert effective["timers"]["logrotate.timer"]["enabled"]
    schedules = kvm.maintenance_schedules(result)
    assert "man-db.timer" not in schedules
    assert "backup@db.timer" in schedules
    assert not any(name.startswith(("apt", "dnf")) for name in schedules)
    assert kvm.MAINTENANCE_PROFILES["balanced"]["fstrim.timer"]["window"] == "01:00-06:00"


@pytest.mark.parametrize("policy", [[], {"profile": []}, {"profile": "oops"}, {"enabled": "true"},
    {"enabled": True}, {"profile": "balanced", "strategy": "random"}, {"typo": True},
    {"profile": "balanced", "timers": []}, {"profile": "custom"},
    {"timers": {"fstrim.timer": {}}},
    *[{"profile": "custom", "timers": {name: {"frequency": "daily", "window": "01:00-06:00"}}}
      for name in ["../evil.timer", "-foo.timer", "foo.service", "foo\n.timer", "foo@.timer", "x" * 250 + ".timer"]],
    *[{"profile": "balanced", "timers": {"fstrim.timer": spec}}
      for spec in [{"window": "23:00-01:00"}, {"window": "25:00-26:00"}, {"window": "01:60-02:00"},
                   {"window": "23:00-24:01"}, {"window": "01:00-01:00"}, {"window": 1},
                   {"frequency": "monthly"}, {"enabled": 1}, {"unknown": 0}, []]]])
def test_invalid_policies(policy):
    with pytest.raises(kvm.KVMError, match="maintenance"):
        cfg(policy)


@pytest.mark.parametrize("frequency", ["daily", "weekly"])
@pytest.mark.parametrize("window", ["00:00-06:00", "01:00-01:01", "23:58-24:00", "01:00-01:16"])
def test_stable_calendar_and_window(frequency, window):
    policy = {"profile": "custom", "timers": {"backup.timer": {"frequency": frequency, "window": window}}}
    start, end = kvm.maintenance_window(window)
    results = set()
    for i in range(128):
        effective = cfg(policy, name=f"vm{i}", mac=f"52:54:00:11:22:{i:02x}")
        schedule = kvm.maintenance_schedules(effective)["backup.timer"]
        assert schedule == kvm.maintenance_schedules(kvm.normalize_definition(copy.deepcopy(effective)))["backup.timer"]
        assert "OnCalendar=\nOnCalendar=" in schedule
        match = re.search(r"OnCalendar=(?:(\w{3}) )?\*-\*-\* (\d\d):(\d\d):00", schedule)
        assert bool(match[1]) == (frequency == "weekly")
        minute = int(match[2]) * 60 + int(match[3])
        jitter = int(re.search(r"RandomizedDelaySec=(\d+)min", schedule)[1])
        assert start <= minute < end
        assert minute + jitter < end
        assert "AccuracySec=1s" in schedule
        assert "Persistent=" not in schedule
        results.add(schedule)
    if frequency == "weekly" or end - start > 16:
        assert len(results) > 1


def test_clone_identity_and_reinstall_schedule():
    policy = {"profile": "balanced"}
    source = cfg(policy)
    target = kvm.normalize_definition(raw(policy, name="clone01", mac="52:54:00:11:22:34"), for_clone=True)
    assert kvm.maintenance_schedules(source) != kvm.maintenance_schedules(target)
    assert kvm.maintenance_schedules(source) == kvm.maintenance_schedules(kvm.normalize_definition(source))


@pytest.mark.parametrize("policy", [None, {"profile": "balanced"}])
def test_cloud_init_integration(tmp_path, monkeypatch, policy):
    monkeypatch.setattr(kvm, "CLOUD_DIR", tmp_path / "cloud")
    effective = cfg(policy, mac="auto")
    effective["cloud_init"]["runcmd"] = [["echo", "user command"]]
    monkeypatch.setattr(kvm, "generate_mac", lambda: "52:54:00:11:22:33")
    with patch.object(kvm, "create_cloud_seed", side_effect=lambda seed, *args: seed.touch()):
        user_data, _, _, _ = kvm.make_cloud_files(effective, tmp_path)
    data = kvm.yaml.safe_load(user_data.read_text())
    assert data["runcmd"][0] == ["echo", "user command"]
    if policy:
        assert data["runcmd"][-1] == ["sh", "-c", kvm.maintenance_command(kvm.maintenance_schedules(effective))]
        assert "fstrim.timer" in data["runcmd"][-1][2]
    else:
        assert len(data["runcmd"]) == 2
    assert effective["cloud_init"]["runcmd"] == [["echo", "user command"]]


def test_clone_cleanup_is_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(kvm, "selected_sysprep_ops", lambda: ["machine-id", "ssh-hostkeys"])
    with patch.object(kvm, "run") as run:
        kvm.sysprep_clone_disk(tmp_path / "clone.qcow2")
    command = run.call_args_list[-1].args[0]
    assert command[:4] == ["virt-customize", "-a", str(tmp_path / "clone.qcow2"), "--run-command"]
    root = tmp_path / "system"
    dropin = root / "fstrim.timer.d"
    dropin.mkdir(parents=True)
    own = dropin / kvm.MAINTENANCE_DROPIN
    other = dropin / "user.conf"
    own.touch()
    other.touch()
    cleanup = next(part.strip() for part in command[-1].split(";") if "rm -f /etc/systemd/system/" in part)
    subprocess.run(["sh", "-c", cleanup.replace("/etc/systemd/system/", str(root) + "/")], check=True)
    assert not own.exists() and other.exists()


def test_guest_application(tmp_path):
    root = tmp_path / "system"
    root.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls"
    fake = bindir / "systemctl"
    fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$*" in
    *fstrim.timer|*logrotate.timer) [ "$1" != show ] || echo loaded ;;
    *e2scrub_all.timer) echo masked; exit 1 ;;
    *man-db.timer|*plocate-updatedb.timer) echo not-found; exit 1 ;;
esac
''')
    fake.chmod(0o755)
    schedules = kvm.maintenance_schedules(cfg({"profile": "balanced"}))
    script = kvm.maintenance_command(schedules).replace("/etc/systemd/system/", str(root) + "/")
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"], CALL_LOG=str(log))
    subprocess.run(["sh", "-c", script], env=env, check=True)
    for timer in ("fstrim.timer", "logrotate.timer"):
        target = root / (timer + ".d") / kvm.MAINTENANCE_DROPIN
        assert target.read_text() == schedules[timer]
        assert target.stat().st_mode & 0o777 == 0o644
    assert len(list(root.iterdir())) == 2
    calls = log.read_text().splitlines()
    assert calls.count("daemon-reload") == 1
    assert "try-restart fstrim.timer" in calls
    assert "try-restart logrotate.timer" in calls
    assert not any(c.startswith(("enable", "start", "restart")) for c in calls)
    log.unlink()
    subprocess.run(["sh", "-c", script], env=env, check=True)
    assert "daemon-reload" not in log.read_text()


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="systemd-analyze unavailable")
def test_systemd_calendar_syntax():
    for value in kvm.maintenance_schedules(cfg({"profile": "balanced"})).values():
        expression = next(line.split("=", 1)[1] for line in value.splitlines() if line.startswith("OnCalendar=") and line != "OnCalendar=")
        subprocess.run(["systemd-analyze", "calendar", expression], check=True, capture_output=True)


@pytest.fixture
def live_vm(tmp_path, monkeypatch):
    for key in ("DEF_DIR", "STATE_DIR"):
        directory = tmp_path / key.lower()
        directory.mkdir()
        monkeypatch.setattr(kvm, key, directory)
    effective = cfg()
    definition = kvm.DEF_DIR / "test01.yaml"
    kvm.save_state(effective, tmp_path / "disk.qcow2", {}, "instance", definition)
    definition.chmod(0o640)
    monkeypatch.setattr(kvm, "require_commands", lambda commands: None)
    monkeypatch.setattr(kvm, "domain_exists", lambda name: True)
    monkeypatch.setattr(kvm, "domain_state", lambda name: "running")
    monkeypatch.setattr(kvm, "domain_macs", lambda name: {effective["network"]["mac"]})
    monkeypatch.setattr(kvm, "check_domain_job", lambda name: None)
    return definition, effective


def cli(*args):
    parsed = kvm.build_parser().parse_args(["maintenance", *args])
    return parsed.func(parsed)


def test_live_preview_and_dry_run(live_vm, monkeypatch, capsys):
    definition, effective = live_vm
    original = definition.read_bytes()
    monkeypatch.setattr(kvm, "domain_state", lambda name: "shut off")
    with patch.object(kvm, "maintenance_guest_exec") as guest:
        assert cli("preview", "test01", "--profile", "balanced") == 0
        assert cli("apply", "test01", "--profile", "balanced", "--dry-run") == 0
        assert cli("remove", "test01", "--dry-run") == 0
        guest.assert_not_called()
    assert "fstrim.timer" in capsys.readouterr().out
    assert definition.read_bytes() == original
    assert not (kvm.STATE_DIR / ".lifecycle-lock").exists()


def test_live_apply_remove_persist_only_policy(live_vm, monkeypatch):
    definition, effective = live_vm
    monkeypatch.setenv("KVM_VM_NAME", "unrelated")
    monkeypatch.setenv("KVM_VM_VCPUS", "99")
    with patch.object(kvm, "maintenance_guest_exec", return_value="configured\n") as guest:
        assert cli("apply", "test01", "--profile", "balanced", "--yes") == 0
        args, kwargs = guest.call_args
        assert args[0] == "test01" and "cloud-init status" in args[1]
        assert "fstrim.timer" in args[1] and "reconcile=1" in args[1]
        assert kwargs == {"mutate": True}
        saved = kvm.yaml_load(definition)
        expected = copy.deepcopy(effective)
        expected["cloud_init"]["maintenance"] = kvm.normalize_maintenance({"profile": "balanced"})
        assert saved == expected
        assert definition.stat().st_mode & 0o777 == 0o640
        assert cli("remove", "test01", "--yes") == 0
        assert kvm.yaml_load(definition) == effective
        assert "apply_timer fstrim.timer" not in guest.call_args.args[1]


def test_custom_policy_file(live_vm, tmp_path):
    definition, _ = live_vm
    policy = tmp_path / "policy.yaml"
    policy.write_text('profile: custom\ntimers:\n  backup.timer:\n    frequency: weekly\n    window: "02:00-03:00"\n')
    with patch.object(kvm, "maintenance_guest_exec", return_value=""):
        assert cli("apply", "test01", "--policy", str(policy), "--yes") == 0
    assert list(kvm.maintenance_schedules(kvm.yaml_load(definition))) == ["backup.timer"]


@pytest.mark.parametrize("fault", ["agent", "offline", "mac", "job", "noninteractive", "cancel", "lock", "unmanaged", "bad-policy"])
def test_live_refusal_preserves_definition(live_vm, monkeypatch, fault, tmp_path):
    definition, _ = live_vm
    original = definition.read_bytes()
    args = ["apply", "test01", "--profile", "balanced", "--yes"]
    if fault == "offline":
        monkeypatch.setattr(kvm, "domain_state", lambda name: "shut off")
    elif fault == "mac":
        monkeypatch.setattr(kvm, "domain_macs", lambda name: set())
    elif fault == "job":
        monkeypatch.setattr(kvm, "check_domain_job", lambda name: (_ for _ in ()).throw(kvm.KVMError("job")))
    elif fault in ("noninteractive", "cancel"):
        args.pop()
        monkeypatch.setattr(kvm.sys.stdin, "isatty", lambda: fault == "cancel")
        monkeypatch.setattr("builtins.input", lambda prompt: "")
    elif fault == "lock":
        (kvm.STATE_DIR / ".lifecycle-lock").mkdir()
    elif fault == "unmanaged":
        (kvm.STATE_DIR / "test01.json").unlink()
    elif fault == "bad-policy":
        policy = tmp_path / "bad.yaml"
        policy.write_text("profile: typo\n")
        args = ["apply", "test01", "--policy", str(policy), "--yes"]
    with patch.object(kvm, "maintenance_guest_exec", side_effect=kvm.KVMError("agent failure")) as guest:
        with pytest.raises(kvm.KVMError):
            cli(*args)
        assert guest.call_count == (1 if fault == "agent" else 0)
    assert definition.read_bytes() == original


def test_live_status_readonly(live_vm, capsys):
    definition, _ = live_vm
    original = definition.read_bytes()
    with patch.object(kvm, "maintenance_guest_exec", return_value="guest timer list\n") as guest:
        assert cli("status", "test01") == 0
        assert "list-timers --all" in guest.call_args.args[1]
        assert not guest.call_args.kwargs
    assert "guest timer list" in capsys.readouterr().out
    assert definition.read_bytes() == original


@pytest.mark.parametrize("result", [
    {"exited": True, "exitcode": 1}, {"exited": True, "signal": 9},
    {"exited": True, "exitcode": 0, "out-truncated": True},
    {"exited": True, "exitcode": 0, "out-data": "!!!"}, {"exited": "yes"},
])
def test_qga_bad_result(result):
    with patch.object(kvm, "maintenance_agent_call", side_effect=[{"pid": 42}, result]):
        with pytest.raises(kvm.KVMError):
            kvm.maintenance_guest_exec("test01", "test")


def test_qga_polling_and_capture(monkeypatch):
    monkeypatch.setattr(kvm.time, "sleep", lambda _: None)
    with patch.object(kvm, "maintenance_agent_call", side_effect=[{"pid": 42}, {"exited": False},
          {"exited": True, "exitcode": 0, "out-data": "aGVsbG8K"}]) as agent:
        assert kvm.maintenance_guest_exec("test01", "test", mutate=True) == "hello\n"
    request = agent.call_args_list[0].args[1]
    assert request["execute"] == "guest-exec"
    assert request["arguments"]["path"] == "/usr/bin/timeout"
    assert "flock" in request["arguments"]["arg"]
    assert agent.call_args_list[-1].args[1] == {"execute": "guest-exec-status", "arguments": {"pid": 42}}


@pytest.mark.parametrize("response", ["{}", '{"error": {"desc": "blocked"}}', "not json", '{"return": []}'])
def test_qga_invalid_rpc(response):
    with patch.object(kvm, "run", return_value=subprocess.CompletedProcess([], 0, response, "")):
        with pytest.raises(kvm.KVMError, match="Invalid guest agent response"):
            kvm.maintenance_agent_call("test01", {"execute": "guest-ping"}, kvm.time.monotonic() + 10)


def test_qga_deadline_and_subprocess_timeout():
    with patch.object(kvm, "run", side_effect=subprocess.TimeoutExpired("virsh", 10)) as run:
        with pytest.raises(kvm.KVMError, match="deadline"):
            kvm.maintenance_agent_call("test01", {}, kvm.time.monotonic() - 1)
        run.assert_not_called()
        with pytest.raises(kvm.KVMError, match="timed out"):
            kvm.maintenance_agent_call("test01", {}, kvm.time.monotonic() + 10)


def test_live_reconciliation_shell(tmp_path):
    root = tmp_path / "system"
    root.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "systemctl"
    fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALL_LOG"
case "$1" in
show) echo loaded ;;
esac
''')
    fake.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, PATH=str(bindir) + ":" + os.environ["PATH"], CALL_LOG=str(log))
    old = root / "old.timer.d"
    old.mkdir()
    (old / kvm.MAINTENANCE_DROPIN).write_text("old")
    (old / "user.conf").write_text("user")
    schedules = {"fstrim.timer": kvm.maintenance_schedules(cfg({"profile": "balanced"}))["fstrim.timer"]}
    for target in (schedules, schedules, {}):
        script = kvm.maintenance_command(target, reconcile=True).replace("/etc/systemd/system/", str(root) + "/")
        subprocess.run(["sh", "-c", script], check=True, env=env)
        assert not (old / kvm.MAINTENANCE_DROPIN).exists()
        assert (old / "user.conf").read_text() == "user"
        assert (root / "fstrim.timer.d" / kvm.MAINTENANCE_DROPIN).exists() == bool(target)
    calls = log.read_text().splitlines()
    assert calls.count("daemon-reload") == 3
    assert calls.count("try-restart fstrim.timer") == 3
    assert calls.count("try-restart old.timer") == 1
    assert not any(c.startswith(("enable", "start", "restart", "disable")) for c in calls)


@pytest.mark.parametrize("status,code,ok", [("done", 0, True), ("disabled", 0, True), ("running", 0, False), ("done", 2, False), ("error", 1, False)])
def test_cloud_init_ready_guard(tmp_path, status, code, ok):
    fake = tmp_path / "cloud-init"
    fake.write_text(f"#!/bin/sh\necho 'status: {status}'\nexit {code}\n")
    fake.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + ":" + os.environ["PATH"])
    result = subprocess.run(["sh", "-c", kvm.MAINTENANCE_READY + "echo applied"], env=env, capture_output=True)
    assert (result.returncode == 0) == ok
    assert (b"applied" in result.stdout) == ok
