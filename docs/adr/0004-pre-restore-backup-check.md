---
status: accepted
---

# Check a Backup directory with kvm-vm before Restore

[ADR 0001](0001-snapshot-and-backup-ownership.md) delegates disk Backup and
Restore to virtnbdbackup and virtnbdrestore, and allows a kvm-vm helper only
for a demonstrated correctness gap. The incremental Restore validation
([ticket 04](../plans/20261018_vm-recovery/ticket-04-validation.md)) found one:

- virtnbdrestore 2.28 and 2.47 apply data and saved configuration files in
  modification-time order, not checkpoint order. A copy that renewed one
  increment's time passed the sealed checksums and `verify`, exited 0, and
  booted a guest with a corrupted file.
- virtnbdrestore exits 0 for a missing increment (2.28), an unknown `--until`
  (2.28) and a missing checkpoint list; 2.47 fails some of these only with a
  Python traceback.

Checksums and `verify` cannot detect the reordering, and a manual comparison
of file times is easy to get wrong in a disaster.

## Decision

Add `kvm-vm backup check DIR [--until virtnbdbackup.N]`. It reads only the
virtnbdbackup output directory and refuses, before any Restore:

- a missing, malformed or non-consecutive checkpoint list, or an unknown point;
- missing full or increment data, their stored checksums, checkpoint metadata
  or saved configuration up to the chosen point, and broken parent links;
- data or configuration files outside the listed history, or whose
  modification-time order differs from the checkpoint order (including equal
  times).

It prints the checkpoints a Restore will apply, exits 1 with the reason
otherwise, and reminds the operator of the remaining checks.

## Scope

- It does not back up, restore, verify stream checksums or change any file.
  `sha256sum -c` on the sealed set and `virtnbdrestore -o verify` remain
  required steps in the [procedure](../full-backup-restore.md).
- It needs no root, libvirt or backup tools, so ordinary kvm-vm operations
  still do not depend on them.
- It understands the virtnbdbackup directory layout tested with 2.28 and 2.47
  (one `*.cpt`, `DISK.full.data`, `DISK.inc.virtnbdbackup.N.data`,
  `checkpoints/`, `vmconfig.virtnbdbackup.N.xml`). Other layouts or tools are
  refused rather than guessed.

## Consequences

kvm-vm now owns one read-only check of an external tool's format and must
follow format changes in future virtnbdbackup versions. The real-machine
harnesses in `tests/` call the same code. This is not the unified backup
command or engine that ADR 0001 rules out.

## Numbering

This ADR was ADR 0002 on the `recovery-validation` branch. It was renumbered
after that branch was merged, because master already used 0002 for the
[Macvtap attachment boundary](0002-macvtap-attachment-boundary.md). Commit
messages written before the renumbering still call it ADR 0002.
