"""The documented backup check must succeed before the restore tool runs."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

import restore_recovery_point as restore
from test_backup_check import build_set


@pytest.mark.parametrize("point, missing, accepted", [(None, False, True), (1, False, True),
                                                      (2, True, False), (9, False, False)])
def test_restore_main_checks_the_chain_through_cli(tmp_path, monkeypatch, point, missing, accepted):
    backup = build_set(tmp_path / "backup")
    if missing:
        (backup / "vda.inc.virtnbdbackup.1.data").unlink()
    if point is not None:
        folder = tmp_path / "points" / str(point)
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps({}))
    argv = ["restore", "--set", str(tmp_path), "--key", str(tmp_path / "key"),
            "--name", "restore-preflight-test"]
    argv += ["--single"] if point is None else ["--point", str(point)]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(restore.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(restore.os, "umask", lambda mode: 0)
    monkeypatch.setenv("LC_ALL", "C")
    original = subprocess.run
    calls = []

    class RestoreReached(Exception):
        pass

    def external(command, **kwargs):
        calls.append(list(command))
        if command[0] == "sha256sum":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[0] == "virsh":
            assert command[3] == "domstate"
            return subprocess.CompletedProcess(command, 1, "", "")
        if command[0] == "kvm-vm":
            # Execute the actual parser and command in a separate process.
            script = Path(__file__).resolve().parents[1] / "kvm-vm"
            return original([sys.executable, str(script), *command[1:]], **kwargs)
        assert command[0] == "virtnbdrestore"
        raise RestoreReached

    monkeypatch.setattr(subprocess, "run", external)
    with pytest.raises(RestoreReached if accepted else subprocess.CalledProcessError):
        restore.main()
    check = [c for c in calls if c[0] == "kvm-vm"]
    assert check == [["kvm-vm", "backup", "check", str(backup)] +
                     ([] if point is None else ["--until", f"virtnbdbackup.{point}"])]
    assert any(c[0] == "virtnbdrestore" for c in calls) == accepted
