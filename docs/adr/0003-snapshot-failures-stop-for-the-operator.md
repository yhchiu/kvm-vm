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
