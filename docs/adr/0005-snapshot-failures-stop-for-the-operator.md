---
status: accepted
---

# Snapshot failures stop for the operator

[ADR 0001](0001-snapshot-and-backup-ownership.md) makes kvm-vm own a bounded
Snapshot lifecycle. The first implementation (tickets 05 and 06) also tried to
continue any interrupted create, delete or rollback when the command was run
again. A code review of the whole batch, verified on a real AlmaLinux 9.8 host,
found that this resume logic was where the defects were:

- A live delete whose block-commit ran out of space part-way was reported as
  "unchanged" and kept as ready. A following rollback booted a mix of old and
  newer data and reported success.
- A Ctrl-C reached `virsh domfsthaw` and killed it. kvm-vm then reported the
  guest as thawed while it stayed frozen.
- An interrupted removal of the record left a directory that blocked every
  command, with no documented way out.

## Decision

kvm-vm does not continue a failed or interrupted Snapshot operation by itself.
It keeps the record with the operation that stopped and the reason, marks the
Snapshot as needing attention, and the status prints the next step. The
documentation lists the manual steps.

- Only a ready Snapshot can be rolled back.
- `snapshot delete` starts a new merge only from a verified state: libvirt lists
  exactly this Snapshot and the VM uses its overlay on the recovery-point disk.
  A merge keeps the current guest data, so running it again is safe.
- When a delete fails, rollback is disabled, because the recovery-point disk
  may already hold newer data.
- When nothing of the Snapshot is in use any more (no libvirt snapshot, and the
  VM uses its standalone disk), `snapshot delete` removes the leftover record
  and an unused overlay.
- While the guest is frozen, kvm-vm ignores SIGINT and SIGTERM, and its virsh
  children inherit that, so the thaw always runs.
- The record is removed with one rename.

## Consequences

Some interruptions now need manual commands that kvm-vm used to run itself,
mainly finishing an interrupted rollback. In exchange there are fewer code
paths that guess at a half-finished state. The capture of management files,
the AppArmor-safe offline commit and the lifecycle refusals stay.

## Considered Options

Reviewed on 2026-10-20, after the change above.

### Only the normal path, with every failure left to the operator

Rejected. The idea was to treat the Snapshot commands as a convenience, so that
the operator does not have to remember virsh arguments, and to leave every
failure to the operator. That only works when the operator can find out where
an operation stopped:

- `snapshot delete` and `snapshot rollback` take several steps, are not atomic
  and change data. After a stop, only kvm-vm knows which step ran. A live
  delete whose block-commit stopped part-way, for example on a full disk, looks
  exactly like a ready Snapshot in `virsh snapshot-list`, but the
  recovery-point disk already holds newer data. A rollback would then boot a
  mix of old and newer data. The status in the record is the only sign.
- These failures are not rare edge cases. The overlay grows with every guest
  write, so a full disk is a likely failure. On Debian 13, a tested host, a
  live delete always fails once QEMU was started with the Snapshot in place
  ([libvirt issue #806](https://gitlab.com/libvirt/libvirt/-/issues/806)).
  There the normal path is a failed live delete, a shutdown and a second
  delete.
- A guest that stays frozen blocks all its writes. That is an outage, and it
  may go unnoticed. Guaranteeing the thaw takes a few lines.
- `clone`, `reinstall` and `delete` refuse a VM with a Snapshot, an incomplete
  Snapshot operation or Backup checkpoints (ticket 02), so that clone does not
  copy old data and delete does not remove storage still in use. They read the
  same state.
- The [spec](../plans/20261018_vm-recovery/spec.md) requires that an
  interrupted operation is never reported as complete, that every guest freeze
  has a thaw path and that an invalid incremental history is never continued
  silently (Implementation Decisions 7 to 9, User Stories 18 and 19). Dropping
  these needs a new ADR that replaces those requirements.

The line this ADR draws:

| Kind | Examples | Decision |
| --- | --- | --- |
| Refuse before any change | libvirt version, UEFI, one Snapshot per VM, Backup checkpoints, guest agent not responding | Keep. No state to track, cheap, and the normal path depends on them. |
| Stop and report what happened | thaw guarantee, record status, check of the result, clear error message | Keep. This is the least the operator needs to take over. |
| Continue by itself | resuming an interrupted create, delete or rollback | Removed by this ADR. |

The Backup side has nothing comparable to cut. kvm-vm does not run Backups.
`kvm-vm backup check` reads files only and keeps no state. It exists because
virtnbdrestore exits 0 on an incomplete or reordered history
([ADR 0004](0004-pre-restore-backup-check.md)): when the tool does not report
the error, leaving errors to the operator does not work. Each checkpoint
refusal is a single `virsh checkpoint-list`. The order "retire the checkpoints,
take the Snapshot, start a new full Backup" comes from libvirt, and refusing
with the command to run is already the simplest option.

### Remove more recovery paths from `snapshot delete`

Possible, not done:

- The removal of a leftover record, and `remove_unreferenced_overlay`, which
  inspects every domain and every image in the disk directory, could become
  manual steps printed by the status.
- The merge from `creating` only happens when kvm-vm is killed during
  `virsh snapshot-create-as`. It could become a manual step.
- The retry from `deleting` stays, because it is the normal Debian 13 path
  described above.

This would save a few dozen lines and some tests. Most of the remaining
complexity comes from the problem itself: external snapshots change the disk
topology, libvirt issue #806 affects AppArmor hosts, Backup checkpoints cannot
be combined with a Snapshot, and libvirt does not restore kvm-vm's own
management files.

### Delete only a shut off VM

Not chosen. It would remove the live block-commit, the riskiest half-finished
state, together with the AppArmor handling. In exchange every delete would need
a shutdown, operators would put deletes off, and the overlay would keep
growing. This is a product trade-off, not a free simplification.

### Hand the Snapshot work to a ready-made tool

No such tool exists; libvirt stays the base. See
[External snapshot tools](../recovery-evaluation.md#external-snapshot-tools).

### Sources

- [Snapshots: failures and interruptions](../snapshots.md#failures-and-interruptions)
  and [AppArmor hosts](../snapshots.md#apparmor-hosts).
- [Implementation specification](../plans/20261018_vm-recovery/spec.md).
- [Ticket 02: guard lifecycle commands](../plans/20261018_vm-recovery/issues/02-guard-lifecycle-recovery-state.md).
- [ADR 0004: check a Backup directory before Restore](0004-pre-restore-backup-check.md).
- [libvirt issue #806](https://gitlab.com/libvirt/libvirt/-/issues/806).
