import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from process_helpers import assert_cleanup_safe, group_running, run_owned_command


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux E2E process management")


@pytest.mark.parametrize("interruption", ["timeout", "keyboard"])
def test_interruption_stops_command_and_stubborn_descendant(tmp_path, monkeypatch, interruption):
    ready = tmp_path / "ready"
    child = "import signal,time; signal.signal(signal.SIGINT,signal.SIG_IGN); time.sleep(60)"
    script = (
        "import os,pathlib,signal,subprocess,sys,time; "
        "signal.signal(signal.SIGINT,signal.SIG_IGN); "
        f"p=subprocess.Popen([sys.executable,'-c',{child!r}]); "
        "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
    )
    original = subprocess.Popen.communicate
    interrupted = False

    def communicate(process, *args, **kwargs):
        nonlocal interrupted
        if interruption == "keyboard" and not interrupted:
            deadline = time.monotonic() + 5
            while not ready.exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            interrupted = True
            raise KeyboardInterrupt
        return original(process, *args, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    exception = KeyboardInterrupt if interruption == "keyboard" else subprocess.TimeoutExpired
    with pytest.raises(exception):
        run_owned_command([sys.executable, "-c", script, str(ready)], timeout=1, grace=0.1)
    assert not group_running(int(ready.read_text()))
    assert_cleanup_safe(tmp_path)


def test_success_and_failure_preserve_output_and_exit_status():
    result = run_owned_command([sys.executable, "-c", "print('completed')"])
    assert result.stdout == "completed\n"
    with pytest.raises(subprocess.CalledProcessError) as failure:
        run_owned_command([sys.executable, "-c", "import sys; print('failure',file=sys.stderr); sys.exit(7)"])
    assert failure.value.returncode == 7
    assert failure.value.stderr == "failure\n"


def test_stale_lock_retains_resources_with_actionable_path(tmp_path):
    lock = tmp_path / ".lifecycle-lock"
    lock.mkdir()
    with pytest.raises(RuntimeError, match="inspect remaining lifecycle lock") as error:
        assert_cleanup_safe(tmp_path)
    assert str(lock) in str(error.value)
    assert lock.exists()


def test_cleanup_requires_successful_domain_inventory(monkeypatch):
    from test_e2e import E2EHelper

    def unavailable(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["virsh", "list"], stderr="libvirt unavailable")

    monkeypatch.setattr(E2EHelper, "virsh", unavailable)
    with pytest.raises(subprocess.CalledProcessError):
        E2EHelper.assert_domain_absent("test-guest")
    monkeypatch.setattr(E2EHelper, "virsh", lambda *a, **kw: subprocess.CompletedProcess([], 0, "test-guest\n"))
    with pytest.raises(RuntimeError, match="retaining its files"):
        E2EHelper.assert_domain_absent("test-guest")
