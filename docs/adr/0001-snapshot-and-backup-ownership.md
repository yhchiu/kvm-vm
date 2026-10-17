---
status: proposed
---

# Own the snapshot lifecycle and delegate disk backups

kvm-vm needs both fast recovery from an unwanted update and disaster recovery
after loss of a host or its storage. Add a bounded snapshot lifecycle backed by
libvirt, while delegating disk backup and reconstruction to virtnbdbackup and
virtnbdrestore. kvm-vm's responsibility in disaster recovery is to document and
validate recovery of its own management information alongside the restored VM.

## Scope

- Keep at most one short-lived snapshot per VM, with creation, inspection,
  rollback and deletion. Capture disks and their corresponding VM configuration;
  RAM and process execution state are outside the first version's scope.
- Create snapshots without shutting down the guest, allowing brief filesystem
  freeze or I/O pauses. Rollback may stop the guest and restart it. Actual
  pause and recovery durations require measurement.
- Operators may use the backup tools directly. Begin backup integration with
  operational documentation, an inventory of required management artifacts and
  a recovery exercise on a compatible replacement host. Successful recovery
  means the VM boots and remains managed by kvm-vm without depending on the
  original host or storage.
- Add backup or metadata helper commands only when a demonstrated correctness
  or operational gap warrants them. Building a new backup engine, a unified CLI
  solely for convenience, and a branching snapshot history are outside this
  proposed scope.

## Consequences

This preserves fast local rollback without taking ownership of incremental
backup formats and data transfer. It still requires real lifecycle integration:
external snapshots change disk topology, rollback can affect incremental backup
tracking, and libvirt does not restore kvm-vm's separate management artifacts.

Fix the reproduced reinstall cleanup failure before claiming support for
checkpoint-based backup workflows. Verify disk preservation on failures and
snapshot/backup interoperability on supported Linux/KVM configurations. The
current discussion has not implemented these features or run a cross-host
recovery exercise.

See the [evaluation](../recovery-evaluation.md) for the confirmed requirements,
code findings, upstream references and proposed commit and validation sequence.
