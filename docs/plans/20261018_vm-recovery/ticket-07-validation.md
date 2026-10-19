# Ticket 07 validation

Status: the supported Snapshot and Backup sequence passed end to end on
2026-10-19/20 (Debian 13 source, AlmaLinux 9.8 destination); review gate pending.

## Supported sequence and decisions

- A Snapshot and incremental Backup tracking are never combined; kvm-vm
  resets the Backup baseline around each Snapshot. The tested sequence:
  1. Take a final incremental Backup if needed.
  2. Retire the tracking while the VM runs (`virsh checkpoint-delete NAME
     virtnbdbackup.0 --children`).
  3. `kvm-vm snapshot create`.
  4. Keep the change with `snapshot delete` or discard it with
     `snapshot rollback`.
  5. Start a new full Backup into a new directory, with per-point management
     capture.

  It is documented in
  [Snapshots and Backups](../../snapshots.md#snapshots-and-backups). Carrying
  checkpoints and bitmaps across an external snapshot, its commit or its
  rollback was not validated and stays refused.
- Integration changes in kvm-vm:
  - `snapshot create`, `delete` and `rollback` name the checkpoints in their
    refusal, say that nothing was changed, and print the supported sequence
    with the exact retire command.
  - A failing `virsh domjobinfo` (seen for a moment while a Backup job starts)
    is now an explicit refusal with a retry hint, not a raw command failure.
- Finding: for a running VM, `qemu-img info` does not list persistent bitmaps
  (QEMU writes them at close; QEMU's own query showed them). kvm-vm therefore
  relies on libvirt checkpoints for running VMs. The documentation warns
  against removing only checkpoint metadata. kvm-vm does not query QEMU's
  monitor directly, because libvirt marks that as unsupported use.

## Source run (Debian 13, libvirt 11.3.0, QEMU 10.0.13, virtnbdbackup 2.28, AppArmor)

`tests/prepare_snapshot_backup_interop.py` with the guest's AppArmor-permitted
socket directory passed for `e2e-sbi-2a9545047ff7` (Debian 12 guest,
isolated `KVM_VM_*` directories; the host's production guests and kvm-vm state
were not touched). Each step asserts its outcome:

| Step | Result |
| --- | --- |
| Old history: full (93.1 s) and incremental (3.2 s) | Checkpoints `virtnbdbackup.0`, `.1`; bitmaps of both seen through QEMU |
| `snapshot create` with that tracking | Exit 1, nothing changed, retire command printed |
| Retire with `checkpoint-delete virtnbdbackup.0 --children` | No checkpoints or bitmaps left; old history still passes `verify` and the chain check |
| Incremental into a copy of the old history | Exit 1 (`Failed to redefine checkpoints`), no data written |
| `snapshot create` while a full Backup job runs | Exit 1 (the job's new checkpoint is refused); the Backup finished with exit 0; its checkpoint was retired afterward |
| Backup killed with SIGKILL during its job | Left a running Backup job, checkpoint `virtnbdbackup.0`, its bitmap and temporary unnamed bitmaps; `snapshot create` exit 1; `domjobabort` and `checkpoint-delete --children` cleaned it all |
| Snapshot (freeze 877 ms), unwanted update (`point-2`, 2 vCPU/1536 MiB, YAML), rollback | Back to `point-0`, `point-1`, 1 vCPU; SSH 44.1 s after the rollback command; no checkpoints or bitmaps |
| Snapshot, kept update (`point-3`), `snapshot delete` | `point-3` kept |
| New history: full (91.5 s), `point-4`, incremental (3.1 s) | Chain check and `verify` passed; 46 files sealed |
| With the new tracking: `snapshot create`, `reinstall`, `delete --keep-storage`, `clone` (shut off) | All exit 1 with the Backup checkpoint refusal |

## Destination restore (AlmaLinux 9.8, libvirt 11.10.0, virtnbdrestore 2.47)

The set was pulled as one tar with a temporary key restricted on the source to
a forced `tar` command. The SHA-256 was `f0613c3b…` on both sides, the key and
`known_hosts` entries were removed before restore, and all 46 sealed files
verified. `tests/restore_recovery_point.py --point 1` passed: set checksums,
chain `virtnbdbackup.0, .1`, `verify`, restore in 9.0 s, SSH 16.4 s after start.
Data, identity, 1 vCPU/1024 MiB, UUID, kvm-vm `status`, `list --managed` and
`validate` all matched point 1. The guest held `point-0`, `point-1`, `point-3`
and `point-4`, without the rolled-back `point-2`. On the restored VM a
Snapshot (93 ms freeze) and a rollback (1.0 s until start) discarded a
post-restore file as expected. The VM was then deleted with
`kvm-vm delete --force --yes`.

## Tests

- `tests/test_snapshot.py`: 59 tests. New: the three checkpoint refusals print
  the supported sequence and change nothing, and a failing job query is an
  explicit refusal. Earlier tickets' tests cover the freeze/thaw path,
  interruptions and the lifecycle refusals.
- Workstation suite: **274 passed, 20 skipped**.
- The source run used the code before the `domjobinfo` refusal change, which
  only affects that failure path. The confirmation with the final code is in
  the [review gate](#review-gate).

## Tested matrix and limits

| | Source | Destination |
| --- | --- | --- |
| OS | Debian 13 | AlmaLinux 9.8 |
| libvirt / QEMU | 11.3.0 / 10.0.13 | 11.10.0 / 10.1.0 |
| virtnbdbackup | 2.28 | 2.47 |
| Security | AppArmor | SELinux permissive |
| Guest | Debian 12, one qcow2 disk, BIOS, guest agent | (restored) |

Not validated: carrying checkpoints across a Snapshot, push-mode or other
backup tools, SELinux enforcing, UEFI, multiple disks, application-consistent
workloads, and lifecycle commands after the killed-Backup case beyond
`snapshot create` (the same checkpoint and job checks apply). Measured times
are single runs on these hosts, not limits.

All test guests, sets, keys, working copies and logs were removed from both
hosts afterward. The destination keeps kvm-vm, the backup tools and cached
images; its SELinux runtime mode is Permissive, as found.

## Review gate

Pending.
