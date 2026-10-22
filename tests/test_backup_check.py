"""`kvm-vm backup check` refuses the sets that virtnbdrestore would restore with exit 0."""
import importlib.util
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import sys

import pytest

_loader = SourceFileLoader("kvm_vm_backup", str(Path(__file__).resolve().parent.parent / "kvm-vm"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
kvm_vm = importlib.util.module_from_spec(_spec)
sys.modules[_loader.name] = kvm_vm
_loader.exec_module(kvm_vm)

ChainError = kvm_vm.BackupChainError
check_backup_chain = kvm_vm.check_backup_chain

CHECKPOINT = """<domaincheckpoint><name>{name}</name>{parent}</domaincheckpoint>"""
BASE_TIME = 1_760_000_000


def build_set(root, points=3, disk="vda", domain="guest"):
    names = ["virtnbdbackup.%d" % i for i in range(points)]
    (root / "checkpoints").mkdir(parents=True)
    (root / (domain + ".cpt")).write_text(json.dumps(names))
    files = [disk + ".full.data"] + ["%s.inc.%s.data" % (disk, n) for n in names[1:]]
    for index, name in enumerate(files):
        (root / name).write_bytes(b"data")
        (root / (name + ".chksum")).write_text("1")
        set_time(root / name, index)
    for index, name in enumerate(names):
        parent = "" if index == 0 else "<parent><name>%s</name></parent>" % names[index - 1]
        (root / "checkpoints" / (name + ".xml")).write_text(CHECKPOINT.format(name=name, parent=parent))
        (root / ("vmconfig.%s.xml" % name)).write_text("<domain/>")
        set_time(root / ("vmconfig.%s.xml" % name), index)
    return root


def set_time(path, step):
    """Backups are seconds apart; a timestamp-preserving transfer keeps that order."""
    os.utime(path, (BASE_TIME + 10 * step, BASE_TIME + 10 * step))


def test_complete_history_selects_the_requested_prefix(tmp_path):
    backup = build_set(tmp_path)
    assert check_backup_chain(backup) == ["virtnbdbackup.0", "virtnbdbackup.1", "virtnbdbackup.2"]
    assert check_backup_chain(backup, "virtnbdbackup.1") == ["virtnbdbackup.0", "virtnbdbackup.1"]
    assert check_backup_chain(backup, "virtnbdbackup.0") == ["virtnbdbackup.0"]


def test_missing_increment_before_the_chosen_point_is_refused(tmp_path):
    backup = build_set(tmp_path)
    (backup / "vda.inc.virtnbdbackup.1.data").unlink()
    with pytest.raises(ChainError, match="missing vda.inc.virtnbdbackup.1.data"):
        check_backup_chain(backup, "virtnbdbackup.2")


def test_later_missing_increment_does_not_block_an_earlier_point(tmp_path):
    backup = build_set(tmp_path)
    (backup / "vda.inc.virtnbdbackup.2.data").unlink()
    assert check_backup_chain(backup, "virtnbdbackup.1")[-1] == "virtnbdbackup.1"
    with pytest.raises(ChainError):
        check_backup_chain(backup)


def test_missing_stored_checksum_is_refused(tmp_path):
    backup = build_set(tmp_path)
    (backup / "vda.full.data.chksum").unlink()
    with pytest.raises(ChainError, match="vda.full.data.chksum"):
        check_backup_chain(backup)


def test_unknown_recovery_point_is_refused_not_widened_to_the_whole_history(tmp_path):
    with pytest.raises(ChainError, match="Unknown recovery point"):
        check_backup_chain(build_set(tmp_path), "virtnbdbackup.9")


def test_missing_checkpoint_list_is_refused(tmp_path):
    backup = build_set(tmp_path)
    (backup / "guest.cpt").unlink()
    with pytest.raises(ChainError, match="checkpoint list"):
        check_backup_chain(backup)


@pytest.mark.parametrize("content", ["not json", "[]", '["virtnbdbackup.1"]', '["virtnbdbackup.0", "virtnbdbackup.2"]'])
def test_malformed_or_gapped_checkpoint_list_is_refused(tmp_path, content):
    backup = build_set(tmp_path)
    (backup / "guest.cpt").write_text(content)
    with pytest.raises(ChainError):
        check_backup_chain(backup)


def test_full_backup_data_is_required(tmp_path):
    backup = build_set(tmp_path)
    (backup / "vda.full.data").unlink()
    with pytest.raises(ChainError, match="full Backup data"):
        check_backup_chain(backup)


def test_checkpoint_metadata_and_parent_links_are_required(tmp_path):
    backup = build_set(tmp_path)
    (backup / "checkpoints" / "virtnbdbackup.1.xml").write_text(
        CHECKPOINT.format(name="virtnbdbackup.1", parent="<parent><name>virtnbdbackup.7</name></parent>"))
    with pytest.raises(ChainError, match="parent"):
        check_backup_chain(backup)
    (backup / "checkpoints" / "virtnbdbackup.1.xml").unlink()
    with pytest.raises(ChainError, match="missing metadata"):
        check_backup_chain(backup)


def test_increment_newer_than_a_later_one_is_refused(tmp_path):
    # virtnbdrestore applies data files oldest first and stops at --until, so a
    # touched inc.1 makes it apply full+inc.2 and skip inc.1 with exit 0.
    backup = build_set(tmp_path)
    set_time(backup / "vda.inc.virtnbdbackup.1.data", 5)
    with pytest.raises(ChainError, match="modification-time order"):
        check_backup_chain(backup, "virtnbdbackup.2")
    with pytest.raises(ChainError, match="modification-time order"):
        check_backup_chain(backup, "virtnbdbackup.1")


def test_equal_modification_times_are_refused_because_the_order_is_undefined(tmp_path):
    backup = build_set(tmp_path)
    set_time(backup / "vda.inc.virtnbdbackup.2.data", 1)
    with pytest.raises(ChainError, match="modification-time order"):
        check_backup_chain(backup, "virtnbdbackup.1")


def test_saved_configuration_files_out_of_order_are_refused(tmp_path):
    backup = build_set(tmp_path)
    set_time(backup / "vmconfig.virtnbdbackup.0.xml", 7)
    with pytest.raises(ChainError, match="vmconfig"):
        check_backup_chain(backup, "virtnbdbackup.2")


def test_data_file_outside_the_listed_history_is_refused(tmp_path):
    backup = build_set(tmp_path)
    (backup / "vda.inc.virtnbdbackup.5.data").write_bytes(b"data")
    set_time(backup / "vda.inc.virtnbdbackup.5.data", 9)
    with pytest.raises(ChainError, match="not in the checkpoint list"):
        check_backup_chain(backup, "virtnbdbackup.1")


def test_invalid_checkpoint_metadata_is_refused(tmp_path):
    backup = build_set(tmp_path)
    (backup / "checkpoints" / "virtnbdbackup.1.xml").write_text("<domaincheckpoint>")
    with pytest.raises(ChainError, match="not valid XML"):
        check_backup_chain(backup)


def test_missing_directory_is_refused(tmp_path):
    with pytest.raises(ChainError, match="not found"):
        check_backup_chain(tmp_path / "absent")


def kvm_vm_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["kvm-vm", *args])
    return kvm_vm.main()


def test_command_reports_the_chain_and_the_remaining_checks(tmp_path, monkeypatch, capsys):
    backup = build_set(tmp_path)
    assert kvm_vm_cli(monkeypatch, "backup", "check", str(backup), "--until", "virtnbdbackup.1") == 0
    out = capsys.readouterr().out
    assert "complete up to virtnbdbackup.1: virtnbdbackup.0, virtnbdbackup.1" in out
    assert "sha256sum -c SHA256SUMS" in out and "-o verify" in out
    assert kvm_vm_cli(monkeypatch, "backup", "check", str(backup)) == 0
    assert "virtnbdbackup.0, virtnbdbackup.1, virtnbdbackup.2" in capsys.readouterr().out


def test_command_refuses_with_exit_1_and_the_reason(tmp_path, monkeypatch, caplog):
    backup = build_set(tmp_path)
    (backup / "vda.inc.virtnbdbackup.1.data").unlink()
    assert kvm_vm_cli(monkeypatch, "backup", "check", str(backup), "--until", "virtnbdbackup.2") == 1
    assert "Do not restore from" in caplog.text and "missing vda.inc.virtnbdbackup.1.data" in caplog.text


def test_command_needs_no_backup_tools_or_libvirt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kvm_vm.shutil, "which", lambda name: None)
    monkeypatch.setattr(kvm_vm.subprocess, "run", lambda *a, **k: pytest.fail("no external command expected"))
    assert kvm_vm_cli(monkeypatch, "backup", "check", str(build_set(tmp_path))) == 0
