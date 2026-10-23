"""Own E2E command groups so interrupted tests cannot race file cleanup."""
import os
from pathlib import Path
import signal
import subprocess
import time


_active_groups = set()


def group_running(group):
    # Linux zombies cannot write files; killpg(0) alone also counts zombies.
    for entry in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = entry.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == group and fields[0] != "Z":
                return True
        except (FileNotFoundError, ProcessLookupError):
            continue
    return False


def stop_group(process, grace):
    try:
        os.killpg(process.pid, signal.SIGINT)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while group_running(process.pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.communicate(timeout=5)
    deadline = time.monotonic() + 5
    while group_running(process.pid):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Command group {process.pid} did not stop; retain test resources")
        time.sleep(0.02)


def run_owned_command(cmd, *, check=True, capture=True, env=None, timeout=1800, grace=10):
    process = subprocess.Popen(cmd, text=True, env=env, start_new_session=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None)
    _active_groups.add(process.pid)
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            if group_running(process.pid):
                raise RuntimeError(f"Command left running descendants: {cmd}")
        except BaseException:
            stop_group(process, grace)
            raise
        result = subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        if check:
            result.check_returncode()
        return result
    finally:
        if not group_running(process.pid):
            _active_groups.discard(process.pid)


def assert_cleanup_safe(state_dir):
    if _active_groups:
        raise RuntimeError(f"Retaining test resources: command groups still active: {sorted(_active_groups)}")
    lock = Path(state_dir) / ".lifecycle-lock"
    if lock.exists():
        raise RuntimeError(f"Retaining test resources: inspect remaining lifecycle lock {lock} and its owner before cleanup")
