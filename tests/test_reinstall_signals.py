"""Real POSIX signals around reinstall's destructive command boundaries.

Host commands are simulated by reinstall_machine; the interrupted command is
a real subprocess. No libvirt host or VM is needed for these integration tests.
"""
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from test_unit import kvm_vm, reinstall_machine  # noqa: F401


pytestmark = pytest.mark.skipif(not hasattr(os, "fork"), reason="Requires POSIX process groups")


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("whole_group", [False, True], ids=["process", "group"])
@pytest.mark.parametrize("stage", ["preparing", "before-undefine", "after-undefine", "after-replacement"])
def test_reinstall_real_signal(reinstall_machine, tmp_path, signum, whole_group, stage):
    machine = reinstall_machine
    machine.update(failure=None, install_succeeds=True)
    external = kvm_vm.subprocess.run
    ready, result = tmp_path / "ready", tmp_path / "result.json"
    pid = os.fork()
    if pid == 0:
        os.setsid()
        signal.signal(signal.SIGINT, signal.default_int_handler)

        def block():
            # Readiness comes from the command itself, after it starts.
            command = subprocess.Popen([
                sys.executable, "-c",
                "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)", str(ready),
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                command.wait()
            finally:
                if command.poll() is None:
                    command.terminate()
                command.wait(timeout=5)

        def interrupted(cmd, **kwargs):
            undefine = cmd[0] == "virsh" and cmd[3] == "undefine"
            if ((stage == "preparing" and cmd[:2] == ["qemu-img", "convert"])
                    or (stage == "before-undefine" and undefine)
                    or (stage == "after-replacement" and cmd[0] == "virt-install")):
                block()
            completed = external(cmd, **kwargs)
            if stage == "after-undefine" and undefine:
                block()
            return completed

        kvm_vm.subprocess.run = interrupted
        try:
            kvm_vm.cmd_reinstall(machine["args"])
        except BaseException as exc:
            result.write_text(json.dumps({"exception": type(exc).__name__, "defined": machine["defined"]}))
        finally:
            os._exit(0)

    reaped = False
    try:
        deadline = time.monotonic() + 15
        while not ready.exists():
            assert time.monotonic() < deadline, "reinstall did not reach the requested command boundary"
            time.sleep(0.02)
        if whole_group:
            os.killpg(pid, signum)
        else:
            os.kill(pid, signum)
        deadline = time.monotonic() + 15
        while True:
            done, _ = os.waitpid(pid, os.WNOHANG)
            if done:
                reaped = True
                break
            assert time.monotonic() < deadline, "interrupted reinstall did not exit"
            time.sleep(0.02)
        outcome = json.loads(result.read_text())
        assert outcome["exception"] == "KeyboardInterrupt"
        assert outcome["defined"] == (stage in ("preparing", "before-undefine"))
        expected = b"replacement guest data" if stage == "after-replacement" else b"original guest data"
        assert machine["disk"].read_bytes() == expected
        assert not (kvm_vm.STATE_DIR / ".lifecycle-lock").exists()
        for path, content in machine["originals"].items():
            if path.parent in (kvm_vm.STATE_DIR, kvm_vm.DEF_DIR):
                assert path.read_bytes() == content
        recovery = list(machine["disk"].parent.glob(".test01-reinstall-*"))
        if stage == "preparing":
            assert not recovery
        else:
            assert len(recovery) == 1
            xml = kvm_vm.ET.parse(recovery[0] / "domain.xml")
            assert xml.find("./devices/disk/source").get("file") == str(machine["disk"])
            note = (recovery[0] / "RECOVERY.txt").read_text()
            assert ("Disk replacement may have started" in note) == (stage == "after-replacement")
    finally:
        # Also terminate a command descendant if the assertion or parent failed.
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not reaped:
            os.waitpid(pid, 0)
