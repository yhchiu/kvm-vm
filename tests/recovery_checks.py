"""Pre-restore checks for a virtnbdbackup directory.

virtnbdrestore exits 0 when an increment file is missing, when --until names an
unknown checkpoint, or when the checkpoint list file is absent, and it restores
a corrupted increment unless 'verify' ran first. It also orders data and saved
configuration files by modification time, not by checkpoint name, so a transfer
that changes timestamps can make it skip or reorder increments with exit 0.
These checks refuse such sets before any restore, so a short, damaged or
reordered history is never reported as success.

Operators run it before virtnbdrestore (see docs/full-backup-restore.md):
    python3 tests/recovery_checks.py SET/backup virtnbdbackup.N
"""

import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


class ChainError(Exception):
    """The Backup directory cannot restore the requested recovery point."""


def _checkpoint_names(backup):
    lists = sorted(backup.glob("*.cpt"))
    if len(lists) != 1:
        raise ChainError("Expected exactly one checkpoint list (*.cpt) in %s, found %d" % (backup, len(lists)))
    try:
        names = json.loads(lists[0].read_text(encoding="utf-8"))
    except ValueError as error:
        raise ChainError("Checkpoint list %s is not valid JSON: %s" % (lists[0].name, error))
    if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
        raise ChainError("Checkpoint list %s is empty or malformed" % lists[0].name)
    for index, name in enumerate(names):
        if not re.fullmatch(r"virtnbdbackup\.%d" % index, name):
            raise ChainError("Checkpoint list is not a consecutive history from virtnbdbackup.0: %r" % names)
    return names


def _disks(backup):
    disks = sorted(p.name[:-len(".full.data")] for p in backup.glob("*.full.data"))
    if not disks:
        raise ChainError("The full Backup data file (*.full.data) is missing")
    return disks


def _check_order(backup, pattern, expected):
    """Require the files virtnbdrestore globs with 'pattern' to sort into checkpoint order."""
    present = sorted(backup.glob(pattern), key=lambda p: p.stat().st_mtime_ns)
    unknown = [p.name for p in present if p.name not in expected]
    if unknown:
        raise ChainError("Files not in the checkpoint list: %s" % ", ".join(unknown))
    ranks = [expected.index(p.name) for p in present]
    times = [p.stat().st_mtime_ns for p in present]
    if ranks != sorted(ranks) or len(set(times)) != len(times):
        raise ChainError(
            "%s files are out of checkpoint modification-time order (%s); virtnbdrestore applies "
            "them oldest first and would restore the wrong data. Re-copy the set with timestamps "
            "preserved (tar, cp -a, rsync -a) or, on a working copy, set the times in checkpoint "
            "order with touch" % (pattern, ", ".join(p.name for p in present)))


def check_backup_chain(backup, until=None):
    """Return the checkpoint names a restore up to 'until' will apply, or raise ChainError.

    until is a checkpoint name such as 'virtnbdbackup.1'; None means the whole history.
    """
    backup = Path(backup)
    names = _checkpoint_names(backup)
    if until is not None and until not in names:
        raise ChainError("Unknown recovery point %r; the set holds %s" % (until, ", ".join(names)))
    selected = names[:names.index(until) + 1] if until else names
    for disk in _disks(backup):
        required = [backup / (disk + ".full.data")]
        required += [backup / ("%s.inc.%s.data" % (disk, name)) for name in selected[1:]]
        for data in required:
            for path in (data, data.with_name(data.name + ".chksum")):
                if not path.is_file():
                    raise ChainError("Incomplete Backup history: missing %s" % path.name)
        expected = [disk + ".full.data"] + ["%s.inc.%s.data" % (disk, name) for name in names[1:]]
        _check_order(backup, disk + "*.data", expected)
    _check_order(backup, "vmconfig*.xml", ["vmconfig.%s.xml" % name for name in names])
    previous = None
    for name in selected:
        meta = backup / "checkpoints" / (name + ".xml")
        if not meta.is_file() or not (backup / ("vmconfig.%s.xml" % name)).is_file():
            raise ChainError("Incomplete Backup history: missing metadata for %s" % name)
        parent = ET.parse(meta).getroot().findtext("parent/name")
        if parent != previous:
            raise ChainError("Checkpoint %s has parent %r, expected %r" % (name, parent, previous))
        previous = name
    return selected


def main(argv=None):
    parser = argparse.ArgumentParser(description="Refuse a virtnbdbackup directory that cannot "
                                                 "restore the chosen recovery point.")
    parser.add_argument("backup", help="The Backup output directory inside the recovery set")
    parser.add_argument("until", nargs="?", help="Checkpoint to restore, e.g. virtnbdbackup.1 "
                                                 "(default: the whole history)")
    args = parser.parse_args(argv)
    try:
        selected = check_backup_chain(args.backup, args.until)
    except ChainError as error:
        print("REFUSED: %s" % error, file=sys.stderr)
        return 1
    print("Backup history complete: " + ", ".join(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
