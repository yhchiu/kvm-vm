# Ticket 04 validation

Status: cross-host acceptance passed on 2026-10-19 (Debian 13 to AlmaLinux 9.8,
synced-marker workload); review gate closed.

Implementation/review fixed point: `4f6581ec7e50c288465594693907b6c184d712f8`.

## Scope and decisions

- [Recovery procedure](../../full-backup-restore.md#incremental-backup-history)
  and [acceptance workflow](../../full-backup-acceptance.md#incremental-history-exercise).
- `tests/prepare_incremental_backup.py` builds the source history and runs the
  invalid-state drill. `tests/restore_recovery_point.py` restores one point on
  the destination. `tests/recovery_checks.py` is the pre-restore chain check
  that both use and that the procedure tells operators to run.
- No kvm-vm command changed. The gaps found here are in how `virtnbdrestore`
  handles a broken or reordered set, not in kvm-vm's management data. The fix
  is a required, documented check before the restore, backed by unit tests.
  Whether to ship that check as a supported helper is left to an ADR-0001
  decision; see [follow-ups](#follow-ups).
- kvm-vm still does not need the backup tools for its other operations.

## Hosts and versions

| | Source | Destination |
| --- | --- | --- |
| OS / kernel | Debian 13, 6.12.101+deb13-amd64 | AlmaLinux 9.8 |
| CPU | Xeon L5420 | Xeon E5-2650 (x86-64-v2) |
| libvirt / QEMU | 11.3.0 / 10.0.13 | 11.10.0 / qemu-kvm 10.1.0 |
| virtnbdbackup | 2.28 (Debian) | 2.47 (EPEL) |
| Python | 3.13.5 | 3.9.25 |
| Security policy | AppArmor; NBD socket in the guest's runtime directory | SELinux runtime Permissive |

The guest is Debian 12 with 1 vCPU, 1024 MiB, a 10 GiB standalone qcow2 disk,
the `default` NAT network and the QEMU guest agent.

## Source history

The final run of
`python3 -u tests/prepare_incremental_backup.py --invalid-state-drill
--socket-template '/var/lib/libvirt/qemu/domain-{id}-{name20}/recovery-nbd-{n}.sock'`
exited 0 with **SOURCE PREPARATION PASSED** for `e2e-inc-56ff532bf050`.

| Point | Checkpoint | Guest change | Persistent config | Backup | Data file size |
| --- | --- | --- | --- | --- | --- |
| 0 | `virtnbdbackup.0` | `point-0`, `point-0.bin` | 1 vCPU, 1024 MiB | full, 56.2 s | 1,620,443,736 bytes |
| 1 | `virtnbdbackup.1` | `point-1`, `point-1.bin` | 1 vCPU, 1024 MiB | inc, 3.3 s | 8,062,023 bytes |
| 2 | `virtnbdbackup.2` | `point-2`, `point-2.bin` | 2 vCPU, 1536 MiB | inc, 3.0 s | 7,734,343 bytes |

Each Backup log shows a filesystem freeze and thaw and no warning or error. The
three `vmconfig.virtnbdbackup.N.xml` files are byte-identical and show the live
1 vCPU/1024 MiB, while `points/2/management/domain.xml` holds 2 vCPU/1536 MiB.
So the tool's saved XML cannot pick a point's configuration; `points/N` can.
The set was verified, sealed (68 files) and the guest was shut down with
autostart disabled.

### Invalid-state drill (source, on copies)

| Case | Result |
| --- | --- |
| Incremental after a guest restart | Exit 0; history continued to `virtnbdbackup.3` |
| Incremental after deleting only the latest checkpoint's metadata | Exit 1: `Bitmap already exists: virtnbdbackup.3` |
| New full Backup into a new directory | Exit 0; libvirt then held only `virtnbdbackup.0` |
| Incremental into the retired old directory | Exit 1: `checkpoint inconsistent: missing or broken bitmap`; no data written |
| Incremental into the new directory | Exit 0; new history `virtnbdbackup.0`, `.1` |

| Damaged copy | `verify` | restore | Chain check | Sealed SHA256SUMS |
| --- | --- | --- | --- | --- |
| Increment 1 missing, `--until .2` | 0 | 0 | Refused | Detects it |
| Increment 2 corrupted, `--until .2` | 1 | 0 | Passes (data check is `verify`'s job) | Detects it |
| Unknown `--until virtnbdbackup.9` | 0 | 0 | Refused | Unchanged set |
| `NAME.cpt` missing | 0 | 0 | Refused | Detects it |

The drill ran with the chain check before its timestamp-order rule was added.
The drill copies keep the original times (hardlinks and `copy2`), so the rule
does not change these results.

## Transfer

The destination pulled the set as one tar stream with a temporary key that the
source restricted to a forced `tar -C ROOT -cf - recovery-set` command. The tar
(1,637,601,280 bytes) had SHA-256
`b1bdba83d4bbab90db217c637c352215c0354ba933d5aee5839ecec0d0335f78` on both
hosts, and `sha256sum -c SHA256SUMS` passed for all 68 files. The key was then
removed from the source `authorized_keys` (the file matched its earlier copy),
and the destination key and `known_hosts` entries were deleted before any
restore. The guest key was supplied separately. The destination steps used no
source credential, mount or agent forwarding.

## Destination restores

| Run | Chain applied | Restore | Boot to SSH | Guest result |
| --- | --- | --- | --- | --- |
| Point 1 | `.0`, `.1`; log ends `Reached checkpoint [virtnbdbackup.1], stopping` | 8.6 s | 16.3 s | `point-0`, `point-1` present with expected hashes, `point-2` absent; 1 vCPU, 1024 MiB |
| Point 2 | `.0`, `.1`, `.2` | 8.6 s | 13.4 s | All six files with expected hashes; 2 vCPU, 1536 MiB |

For both points the UUID matched the saved XML; machine-id, cloud-init
instance ID, hostname and all SSH host-key hashes matched `points/N`. kvm-vm
`status`, `list --managed` and `validate` passed. The script mapped the emulator
to `/usr/libexec/qemu-kvm` and the machine type to the destination's q35
canonical type, as in ticket 03. The point 1 guest was removed with
`kvm-vm delete --force --yes` before the point 2 restore.

### Refusals on the destination

Each case stopped before any disk, cloud file, YAML, state file or domain was
created; the default roots stayed empty.

| Case | Refusal |
| --- | --- |
| Increment 1 missing from a sealed copy | `Refusing to restore: the sealed set does not match SHA256SUMS` (first run printed a raw `CalledProcessError`; the script now reports it as an actionable refusal) |
| Same copy resealed without increment 1 | `Refusing to restore: Incomplete Backup history: missing vda.inc.virtnbdbackup.1.data` |
| Increment 1 given a newer modification time (point 1 and point 2) | `Refusing to restore: vda*.data files are out of checkpoint modification-time order (...)` |

### Timestamp order: a silent wrong restore

`virtnbdrestore` 2.28 and 2.47 both sort data files with
`files.sort(key=os.path.getmtime)` (`libvirtnbdbackup/common.py`) and stop at
the first file that reaches `--until`. On a byte-identical copy where only
increment 1's modification time was renewed:

- SHA256SUMS passed, `verify` exited 0 and the restore exited 0.
- `--until virtnbdbackup.1` applied full, increment 2, then increment 1.
  `qemu-img compare` against a correct point 1 restore reported
  `Content mismatch at offset 285212672`. The guest file list still matched
  point 1, because increment 1's metadata blocks were written last.
- `--until virtnbdbackup.2` applied full and increment 2 and stopped, never
  applying increment 1. The guest booted, but `point-1.bin` had hash
  `d722a92f...` instead of `19116fea...`. The restore script's guest check
  caught this only because the expected hashes were recorded.

The chain check now refuses this set. Setting the times in checkpoint order
with `touch -d` on the working copy made the check pass, and that copy restored
a disk that `qemu-img compare` reported identical to the original set's.

### virtnbdrestore 2.47 on damaged copies

| Case | `verify` | restore |
| --- | --- | --- |
| Increment 1 missing, `--until .2` | 0 | 1, `IndexError: list index out of range` traceback |
| Unknown `--until virtnbdbackup.9` | not run | 1, `IndexError` traceback |
| Increment 1 corrupted | 1 (`Stored sums do not match`) | 0 |
| `NAME.cpt` missing | 0 | 0 |

Version 2.47 refuses two cases that 2.28 accepted, but only with a traceback.
It still restores a corrupted increment and a set without its checkpoint list.

## Next Backup history

On the restored point 2 guest (no checkpoints, no bitmaps in the qcow2):

1. `virtnbdbackup -l inc` into a copy of the old Backup directory exited 1 with
   `Redefining checkpoint failed: [virtnbdbackup.0]: checkpoint inconsistent:
   missing or broken bitmap`. The copy's data files were unchanged and the
   domain had no checkpoint or bitmap afterward.
2. A new full Backup into a new set (point 0) and, after writing
   `/var/tmp/point-next-1` and an 8 MiB `point-next-1.bin`, an incremental
   Backup (point 1) succeeded, each with freeze and thaw logged. Data files:
   1,632,764,668 and 14,026,766 bytes. `verify` passed and the set was sealed
   (50 files). Per-point capture used the commands now in the procedure.
3. `kvm-vm delete` refused the VM: `Backup checkpoints exist ... explicitly
   retire its incremental baseline`. With the guest shut off,
   `virsh checkpoint-delete VM virtnbdbackup.0 --children` failed: `cannot
   delete checkpoint for inactive domain`. After starting the guest it removed
   both checkpoints and both bitmaps (`qemu-img info` listed none). The guest
   was then deleted with `kvm-vm delete --force --yes`.
4. `restore_recovery_point.py --set NEXT --point 1` passed: restore 8.9 s, SSH
   13.5 s after start. The guest held all eight marker files with the expected
   hashes, including `point-next-1` and `point-next-1.bin`. Identity matched and
   kvm-vm management checks passed.

The first attempt of step 4 exposed a bug in the restore script. When the
source and destination paths are the same, its leftover-path check fired. The
check now ignores paths that did not change.

## Tests

- `tests/test_recovery_checks.py`: 17 tests. They cover the consecutive
  checkpoint list, required data, checksum and metadata files, parent links, an
  unknown point, data files outside the history, modification-time order for
  data and `vmconfig` files, equal times, and the command-line refusal.
- Full suite on the operator workstation (Windows, Python 3.11.9):
  **216 passed, 18 skipped** (the skipped tests need a Linux KVM host).
- Unit suite on the destination (Python 3.9.25, `-m "not e2e"`):
  **220 passed, 14 deselected**. A first run without `-m "not e2e"` started the
  real distro E2E tests on this root KVM host; it was stopped and left no
  domain.
- The documented `python3 tests/recovery_checks.py SET/backup virtnbdbackup.N`
  ran on the destination against the real sets: exit 0 for points 0, 1 and 2
  and for the next history, and exit 1 with `REFUSED: Unknown recovery point`
  for `virtnbdbackup.7`. The procedure's `.cpt` check passed for the right
  point and failed for a wrong one. Documentation Bash blocks passed `bash -n`;
  relative links, anchors and `git diff --check` passed.
- Real-machine evidence: the runs above. Exit status alone was not accepted;
  every successful restore was checked inside the booted guest.

## Retained resources

None. With the operator's agreement, all test guests, recovery sets, guest
keys, working copies and logs of tickets 03 and 04 were removed from both
hosts after the evidence above was recorded. On the source, only the
`e2e-full-*` and `e2e-inc-*` domains and roots were removed; the unrelated
guests were not touched and `authorized_keys` matches its state before the
run. The destination keeps the installed packages, kvm-vm and the cached
AlmaLinux 9 and Rocky 9 images for later tickets; its SELinux runtime mode is
Permissive, as found.

## Follow-ups

- Decide under ADR-0001 whether the pre-restore chain check becomes a supported
  helper instead of a script in `tests/`. The demonstrated gap is a silent wrong
  restore from file timestamps, plus exit 0 for several broken sets.
- Consider reporting the timestamp ordering and the exit-0 cases to the
  virtnbdbackup project.
- Not covered: SELinux Enforcing during Backup on the destination, multi-disk
  VMs, UEFI, compressed streams, and application-consistent workloads.

## Review gate

Review of `60eebd0...4f6581e`: test-side scripts, unit tests, documentation and
evidence. No kvm-vm runtime code changed.

- Standards: both commits use Conventional Commits with a detailed body and
  no co-author trailer. The new scripts follow `tests/prepare_full_backup.py`
  (root check, `run` helpers, printed pass markers, fenced source). Relative
  links and anchors resolve, Bash blocks pass `bash -n`, and `git diff --check`
  is clean.
- Spec: each issue 04 item maps to this record. Procedure and checkpoint/data
  distinction: the new procedure section. Per-point management files and the
  wrong-pairing demonstration: point 1 restored with 1 vCPU/1024 MiB while
  later files hold 2 vCPU/1536 MiB, and the identical `vmconfig` files. Distinct
  host and independent material: the transfer and destination sections. Next
  history: refused continuation, new full plus incremental, and a restore that
  showed the post-restore data. Invalid tracking and incomplete history: the
  drill and destination refusals. Optional tooling: no runtime change, unit
  suites pass without the backup tools on the workstation.
- Findings fixed during the run, before the commits: an actionable message for
  a SHA256SUMS mismatch, and the false leftover-path refusal on same-path
  restores. No must-fix findings remain.
- Advisory: operators need the kvm-vm source tree for the chain check. This is
  recorded under [follow-ups](#follow-ups) as an ADR-0001 decision.

This gate used one inline review pass instead of separate review agents. The
code is test-side only, and every claim was checked against the real-machine
output above.
