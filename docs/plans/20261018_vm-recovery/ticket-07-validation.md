# Ticket 07 validation

Status: the supported Snapshot and Backup sequence passed end to end on
2026-10-19/20 (Debian 13 source, AlmaLinux 9.8 destination); review gate closed.

Implementation/review fixed point: `83295b862b13ebd5414a12c3b801ff35b18e3f87`
(kvm-vm unchanged since `fe01849a363ac9b47e3a9e70d650b9cd4bb6d574`).

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

- `tests/test_snapshot.py`: 58 tests. New: the three checkpoint refusals print
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
workloads, and `clone` after the killed-Backup case (it needs a shut off VM;
the same checkpoint check applies). Measured times are single runs on these
hosts, not limits.

All test guests, sets, keys, working copies and logs were removed from both
hosts afterward. The destination keeps kvm-vm, the backup tools and cached
images; its SELinux runtime mode is Permissive, as found.

## Review gate

Review of `70f3244...fe01849`.

- Standards: Conventional Commit with a detailed body and no co-author
  trailer. The kvm-vm changes reuse the existing helpers and refusal style.
  The harness follows the earlier `prepare_*` scripts and asserts every step.
  Links, anchors, documentation shell blocks (`bash -n`) and `git diff
  --check` pass.
- Spec: each issue 07 item maps to this record:
  - End-to-end sequence with Restore on another host.
  - Checkpoint and bitmap state before and after retiring and rolling back.
  - The new full baseline, and refusal of the old history.
  - Refusals that explain the alternative without discarding Backups.
  - Invalid tracking, a conflicting Backup job and a killed Backup.
  - Lifecycle rechecks.
  - Consistency and tool exclusions in the documentation.
  - New command-boundary tests and a published matrix.
- Findings:
  1. The killed-Backup step rechecked only `snapshot create`, not `reinstall`,
     `delete` and keep-storage as the checklist asks. Fixed in `83295b8`; the
     harness now asserts that all three are refused while the job and
     checkpoint remain.
  2. `fe01849` and this record said 59 snapshot tests; there are 58. The
     record is corrected.
- Advisory, not changed: the lifecycle refusals from ticket 02 do not print
  the retire command that the snapshot refusals print.

Confirmation with the final code (`kvm-vm` SHA-256 `80ff3282…`, checked on each
host):

| | Debian 13 | AlmaLinux 9.8 |
| --- | --- | --- |
| Unit tests (three files, `-m "not e2e"`) | 274 passed | 274 passed |
| Real-machine run | `prepare_snapshot_backup_interop.py` passed again (`e2e-sbi-7ed410a9a6ec`), including the new lifecycle checks after the killed Backup; rollback to SSH 43.6 s | `tests/test_snapshot_e2e.py`: 2 passed in 257 s; freeze 108 ms; rollback to SSH 21.4 s |

The destination restore was not repeated: kvm-vm and
`restore_recovery_point.py` are the same for the earlier restore, and the
change only affects a failure path. Both hosts were left without test guests,
sets or logs. This gate used one inline review pass instead of separate review
agents.

## Review follow-up: lifecycle success coverage (2026-10-23)

Status: acceptance cases added; real-host execution pending. The historical
results above do not cover these new cases.

`tests/test_snapshot_e2e.py::test_lifecycle_after_snapshot_recovery` now runs
the lifecycle sequence after each of:

- Successful offline Snapshot deletion.
- Successful Snapshot rollback.
- A failed live Snapshot deletion followed by the documented offline deletion.

Each case verifies the expected guest marker hashes and standalone disk, then
clones and boots the guest and checks the copied data. It explicitly deletes
the clone. On the original VM it verifies `delete --keep-storage` preserves the
disk hash and every managed file, defines the retained XML again, reinstalls
and boots a fresh guest with a new instance ID, and explicitly deletes it.
Domain absence and managed file removal are asserted; fixture teardown is not
counted as successful delete acceptance. Teardown also owns the unique clone
name and refuses file removal if its domain could not be undefined.

The failed-delete case uses a PATH-local virsh wrapper only for that CLI call.
It fails the live deletion before committing any blocks; all inspections and
subsequent offline recovery use real libvirt/QEMU. It verifies rollback stays
disabled until recovery finishes. This is deterministic lifecycle coverage,
not a replacement for the partial-write/disk-full evidence in the Snapshot
simplification validation record.

Local attempt on Debian WSL as root collected all five Snapshot E2E cases and
skipped them because `virsh` is unavailable. No real-host success is claimed.
Run on a disposable-capacity Linux/KVM host with the usual guest image and
libguestfs dependencies:

```sh
sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s -p no:cacheprovider
```

Record the revision, libvirt/QEMU versions, host security mode, guest image and
the three `SNAPSHOT_LIFECYCLE_PASSED` lines before closing real-host acceptance.
