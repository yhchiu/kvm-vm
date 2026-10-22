# Ticket 06 validation

Status: implementation, command-boundary tests and Linux/KVM acceptance passed
on 2026-10-19 on two hosts; review gate closed.

Implementation/review fixed point: `035d169ef0f3d0e768baec41ba65819de71e0464`.

Later change (2026-10-20): failed or interrupted Snapshot operations are no longer continued
automatically; see [ADR 0005](../../adr/0005-snapshot-failures-stop-for-the-operator.md) and the
[simplification record](snapshot-simplification-validation.md). The retry behavior described
below is historical.

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

Review of `748d24a...035d169`.

- Standards: Conventional Commit with a detailed body and no co-author
  trailer. The rollback command follows the existing destructive-command
  conventions (`--yes`, `--force` to stop a running VM as in `reinstall`,
  `--no-start`), the `run`/`output` helpers and `lifecycle_exclusive`. Tests
  assert guest data, configuration, files and records.
- Spec: each issue 06 item maps to this record. CLI and confirmation are
  covered by the refusal tests on both levels. Disk and configuration restore:
  real-KVM vCPU/memory and data checks. Coherent management state and identity:
  byte-identical YAML, state and cloud files, unchanged machine ID, instance ID,
  host keys, UUID and MAC, no clone customization. Consume semantics: documented,
  status none afterward, next Snapshot works. Checkpoints: refused before any
  change with the new-full-Backup guidance. Failure injection: interruptions
  at destroy, define and metadata removal, a failed redefine and a failed start.
  Duration: measured on both hosts.
- No must-fix findings. Advisory: interruptions were injected in the
  command-boundary tests only; the real runs covered the successful paths.

Confirmation with `035d169` (`kvm-vm` SHA-256 `3865d3dd…`, checked on each
host); the whole `tests/test_snapshot_e2e.py` (create/delete and rollback) ran:

| | AlmaLinux 9.8 | Debian 13 |
| --- | --- | --- |
| Unit tests (three files, `-m "not e2e"`) | 270 passed | 270 passed |
| `tests/test_snapshot_e2e.py` | 2 passed in 256 s | 2 passed in 714 s |
| Rollback until VM started / to SSH | 1.0 s / 21.5 s | 4.6 s / 45.6 s |
| Shut off rollback with `--no-start` | 0.4 s | 1.0 s |
| Snapshot freeze | 99 to 104 ms | 866 to 1630 ms |

Both hosts were left without test guests. This gate used one inline review
pass instead of separate review agents.
