# Ticket 06 validation

Status: implementation, command-boundary tests and Linux/KVM acceptance passed
on 2026-10-19 on two hosts; review gate pending.

## Decisions

- `kvm-vm snapshot rollback NAME [--yes] [--force] [--no-start]`; documented in
  [Snapshots](../../snapshots.md#roll-back).
- Rollback uses up the Snapshot. kvm-vm does not call libvirt's revert. In
  order, it: stops the VM (`--force` for a running VM); restores the captured
  YAML, state manifest and cloud-init files; defines the captured
  pre-snapshot domain XML, which points at the untouched base disk; removes
  libvirt's snapshot metadata and the overlay; removes the record; and starts
  the VM. Every step can be repeated, and the record stays `rolling-back`
  until the end. While it is `rolling-back`, `snapshot delete` and the
  lifecycle commands refuse.
- Refused before any change: no Snapshot or a Snapshot that is not ready,
  snapshots kvm-vm did not create, Backup checkpoints (they describe
  discarded data; a new full Backup is required), captured XML that does not
  match the record, a base disk that is not a standalone qcow2, no `--yes`,
  and a running VM without `--force`.

## Tests

- `tests/test_snapshot.py`: 54 tests (13 new for rollback). They cover the
  restore of guest data, vCPU configuration, YAML, state and seed; shut off
  with `--no-start`; the confirmation and `--force` refusals; the unsafe
  states; and an interruption after destroy, after define and after metadata
  removal, each finished by a retry while delete and lifecycle commands
  refuse meanwhile. They also cover a failed redefine that can be retried and
  a start failure reported as a completed rollback. Workstation suite:
  **270 passed, 20 skipped**.
- `tests/test_snapshot_e2e.py::test_snapshot_rollback`: a real guest. The
  test writes a marker and records the identity (machine ID, instance ID,
  host keys, hostname), the persistent vCPU, memory, UUID and MAC, and the
  managed files. It then creates a Snapshot and makes an unwanted update
  (new data, 2 vCPU/1536 MiB, YAML change). It checks both refusals and runs
  `rollback --yes --force`. It verifies that only the captured data is
  present, the guest restarted, the identity, configuration and managed files
  are identical, the status is none, the VM is listed as managed, `validate`
  passes and the disk is standalone. It then checks that the next Snapshot
  works, and does a shut-off rollback with `--no-start`.

| | AlmaLinux 9.8 (libvirt 11.10) | Debian 13 (libvirt 11.3, AppArmor) |
| --- | --- | --- |
| Rollback until VM started (running VM) | 1.1 s | 3.8 s |
| Rollback start to guest SSH | 21.5 s | 43.6 s |
| Shut off rollback, `--no-start` | 0.4 s | 0.9 s |
| Result | 1 passed in 124 s | 1 passed in 320 s |

These are single measurements on the tested hosts, not limits. Both hosts
were left without test guests. A first run failed only because the test
expected the confirmation refusal before the running-VM refusal; the test was
corrected and the code was unchanged.

## Not covered

Interruptions on a real host (simulated only), SELinux enforcing, UEFI, and
rollback with Backup checkpoints (refused; ticket 07).

## Review gate

Pending.
