# Ticket 05 validation

Status: implementation, command-boundary tests and Linux/KVM acceptance passed
on 2026-10-19 on two hosts; review gate closed.

Implementation/review fixed point: `89546e00c61dd568fead59a0b7421f158eb0e38b`.

Later change (2026-10-20): failed or interrupted Snapshot operations are no longer continued
automatically; see [ADR 0003](../../adr/0003-snapshot-failures-stop-for-the-operator.md) and the
[simplification record](snapshot-simplification-validation.md). The retry behavior described
below is historical.

## Scope and decisions

- Commands: `kvm-vm snapshot create NAME [--no-quiesce] [--dry-run]`,
  `kvm-vm snapshot status NAME` and `kvm-vm snapshot delete NAME [--yes]`;
  `kvm-vm status` shows the same summary. Operator documentation:
  [Snapshots](../../snapshots.md).
- Mechanism: a libvirt external disk-only snapshot. The VM disk becomes the
  read-only recovery point and `NAME.kvm-vm-<UTC>.qcow2` in the same directory
  takes new writes. Online internal disk-only snapshots are rejected by
  libvirt's QEMU driver, and internal snapshots with memory would pause the
  guest for the RAM save; both were ruled out in the
  [evaluation](../../recovery-evaluation.md).
- kvm-vm freezes and thaws the guest filesystems itself (`domfsfreeze`,
  `snapshot-create-as`, `domfsthaw` in a `finally`), instead of libvirt's
  `--quiesce`. This makes the thaw path testable and the pause measurable.
- A record in `KVM_VM_STATE_DIR/snapshots/NAME/` is written before any change.
  It holds `record.json` (status `creating`, `ready` or `deleting`) and copies
  of the persistent and live XML, effective YAML, state manifest and cloud-init
  files for the later rollback.
- Delete: a running VM uses libvirt's live commit (`virsh snapshot-delete`). A
  shut off VM is committed by kvm-vm with `qemu-img commit`, the domain is
  redefined on the base disk and only libvirt's snapshot metadata is removed.
  The reason is the AppArmor finding below.
- Supported profile, checked before any change: libvirt daemon 9.9 or newer, a
  kvm-vm managed VM, running or shut off, one standalone qcow2 system disk with
  agreeing paths, CD-ROMs excluded, BIOS only (no loader, NVRAM or TPM), no
  Backup checkpoints, no other libvirt snapshot, no domain or block job.
- Backup checkpoints are refused explicitly; Snapshots with incremental Backup
  tracking are for ticket 07. `clone`, `reinstall` and `delete` (also
  `--keep-storage`) refuse a VM with a Snapshot record.
- Rollback is not part of this ticket (ticket 06).

## Spike on libvirt 11.10 (AlmaLinux 9.8)

Plain `virsh` on a disposable guest, before writing kvm-vm code:

| Operation | Observation |
| --- | --- |
| External disk-only create, running | Live and persistent XML move to the overlay with the base as backing store; no reboot; overlay `qemu:qemu 0600` |
| Second snapshot | libvirt accepts it and builds a chain, so kvm-vm must enforce the limit itself |
| Delete, running | Overlay committed and removed in 75 ms, writes kept, persistent vCPU change kept |
| Delete, shut off | Overlay committed and removed by libvirt |
| Revert (for ticket 06) | Domain ends shut off, persistent config of the snapshot restored, newer overlay discarded, a new `NAME.<epoch>` overlay created |
| Shut off domain XML | No `<backingStore>`; the chain must be read from the image |
| `kvm-vm delete` before this ticket | Refused with a generic snapshot message |

The spike guest was deleted afterward.

## AppArmor finding (Debian 13, libvirt 11.3)

The first Debian run failed at the offline delete: `unable to execute QEMU
command 'block-commit': Could not open '<base>': Permission denied`. No AppArmor
denial was logged. A reproduction started a never-booted guest paused after an
offline Snapshot and read its generated profile:

```text
"<disks>/NAME.kvm-vm-<UTC>.qcow2" rwk,
"<disks>/NAME.qcow2" rk,
deny "<disks>/NAME.qcow2" w,
```

After the failed commit libvirt appended `rwk` rules for the base, but the
explicit `deny` wins and is not audited. The live delete of that guest failed
the same way, so the problem is not limited to offline deletion: it happens
whenever QEMU starts with the Snapshot already in place. It matches
[libvirt issue #806](https://gitlab.com/libvirt/libvirt/-/issues/806). A failed
live delete changed nothing: the guest stayed on the overlay and the snapshot
remained.

kvm-vm therefore commits shut off VMs with `qemu-img commit` outside libvirt's
confinement, and when libvirt refuses a live delete it verifies that nothing
changed, sets the record back to `ready` and tells the operator to shut down
and retry. Profiles were not edited and AppArmor was not disabled. The
reproduction guest was removed.

## Command-boundary tests

`tests/test_snapshot.py` (39 tests in `4196ab0`, 41 after the review fixes) runs the real command handlers and
filesystem with libvirt and qemu-img simulated at `subprocess.run`. The
simulation follows the spike, including XML without a chain for shut off
domains. Assertions are on guest data, files, records and the frozen state:

- Create keeps the VM running, thaws it, records a ready Snapshot and captures
  XML, YAML, state and cloud files; dry run changes nothing.
- Delete keeps the post-Snapshot writes for running and shut off VMs, removes
  the overlay and record, and a new Snapshot can then be created.
- A second create is refused with all files unchanged.
- Status: none (exit 0), ready (exit 0), incomplete with guidance (exit 1),
  other libvirt snapshots (exit 1); `kvm-vm status` shows the section.
- Failure after freeze, interruption before or after libvirt, a failed create
  that left an overlay, a freeze failure and a thaw failure (reported with the
  `domfsthaw` command) all end with a thawed guest or an explicit warning,
  and a record only when something changed.
- Agent missing: refused, or crash-consistent with `--no-quiesce`.
- Refused before any change: Backup checkpoints, other snapshots, backing file,
  bitmaps, UEFI loader, floppy device, libvirt 9.8, paused VM, active job,
  unmanaged VM, held lifecycle lock.
- `delete`, `delete --keep-storage`, `reinstall` and `clone` refuse a VM with
  a Snapshot, with all files unchanged.
- Interrupted live and offline deletion (after the commit and after the
  redefine) is finished by running delete again; a live delete refused by
  libvirt leaves the Snapshot ready and the offline delete then succeeds.
- A leftover overlay is removed only when no domain and no readable image uses
  it; otherwise it is kept with a warning.

Full suite on the workstation (Windows, Python 3.11.9) at `4196ab0`: **255
passed, 19 skipped** (the skipped tests need a Linux KVM host). The same unit tests
(`test_unit.py`, `test_snapshot.py`, `test_recovery_checks.py`) passed on both
hosts with the final code: **255 passed** on Python 3.9.25 and on 3.13.5.

## Linux/KVM acceptance

`tests/test_snapshot_e2e.py` drives only the CLI and guest SSH:

1. Create a guest, wait for cloud-init and the guest agent, write an 8 MiB
   random file and a marker, record their hashes and the boot ID.
2. `snapshot create` while running: same hashes and boot ID afterward (no
   reboot), status ready, `qemu-img --backing-chain` shows overlay then base.
3. Second create refused; `delete`, `delete --keep-storage` and `reinstall`
   refused; the guest keeps running.
4. Write more data, `snapshot delete`: same boot ID, new data present, overlay
   gone, base standalone, no libvirt snapshot or record, status none.
5. Create and delete again, then a crash-consistent Snapshot.
6. Shut off: create (offline), `clone` refused, start on the overlay, write,
   shut down, delete, start, data present.
7. Create while running, restart the VM, write, then delete while running. It
   must either work or be refused with the Snapshot unchanged and ready, after
   which an offline delete must work.

| | AlmaLinux 9.8 | Debian 13 |
| --- | --- | --- |
| Host | Xeon E5-2650, libvirt 11.10.0, QEMU 10.1.0, SELinux permissive | Xeon L5420, libvirt 11.3.0, QEMU 10.0.13, AppArmor |
| Guest | AlmaLinux 9 (XFS) | Debian 12 (ext4) |
| kvm-vm directories | Defaults (no other VMs) | Isolated `KVM_VM_*` overrides; the host's production guests and kvm-vm state untouched |
| Filesystem freeze, three creations | 104, 99, 102 ms | 1259, 892, 937 ms |
| Live delete after writing an 8 MiB file | 0.5 s | 2.1 s |
| Delete after a VM restart | Live delete worked | Live delete refused, Snapshot unchanged; offline delete worked |
| Result | **1 passed** in 131 s | **1 passed** in 359 s |

An earlier Alma run measured 98 and 95 ms. The freeze time covers
freeze, snapshot creation and thaw as kvm-vm measures them; it is not a
numerical limit. Both hosts were left without test guests or files.

These runs used the code before two later changes: a comment and keeping an
overlay when an image in the disk directory cannot be inspected. Neither path
runs in this test; the confirmation run with the committed code is in the
[review gate](#review-gate).

## Not covered

- Rollback (ticket 06) and Snapshots with Backup checkpoints (ticket 07).
- SELinux enforcing, UEFI, multiple disks, raw disks, non-default storage.
- Thaw failure, interruption and leftover overlays on a real host (simulated
  only), a guest-initiated reboot, disk-full during commit, and long-lived
  Snapshots with large overlays.
- Application consistency: the workload is synced marker files.

## Review gate

Review of `2127ac7...4196ab0` (implementation, tests and documentation).

- Standards: Conventional Commit with a detailed body and no co-author
  trailer. The new code follows the existing command style (`run`/`output`
  helpers, `KVMError`, `lifecycle_exclusive`, `--yes` confirmation, `--dry-run`).
  Tests use the existing command-boundary approach and assert data, files and
  records, not call order. Links, anchors and `git diff --check` pass.
- Spec: each issue 05 item maps to this record. The supported profile and
  refusals, creation without shutdown with a measured freeze, the one-Snapshot
  limit and the three inspection states, the record and its interrupted
  states, delete keeping writes with a following Snapshot, the lock, job
  checks and lifecycle refusals, the thaw path, the Backup checkpoint refusal,
  and real-KVM evidence on two hosts.
- Must-fix findings, fixed in `89546e0`:
  1. With a snapshot that kvm-vm did not create, `snapshot create` told the
     operator to run `kvm-vm snapshot delete`, which refuses such snapshots.
     The message now points to virsh.
  2. Checkpoints created by a Backup tool while a Snapshot exists were not
     checked on delete, so the overlay could be committed together with
     unverified bitmaps. Delete now refuses and changes nothing until the
     checkpoints are retired.
- Advisory, not changed: the record's live XML is a reference copy only;
  rollback (ticket 06) decides which configuration it restores.

Confirmation runs with the final code (`89546e0`, `kvm-vm` SHA-256
`081a7b5a…`, checked on each host):

| | AlmaLinux 9.8 | Debian 13 |
| --- | --- | --- |
| Unit tests (`-m "not e2e"`, three files) | 257 passed | 257 passed |
| `tests/test_snapshot_e2e.py` | 1 passed in 132 s | 1 passed in 372 s |
| Filesystem freeze | 101 and 103 ms | 880, 1288 and 1329 ms |
| Live delete | 0.6 s | 2.4 s |
| Delete after a VM restart | Live delete worked | Refused online, then deleted offline |

`4196ab0` also passed on both hosts (Alma 100 to 103 ms; Debian 958 to
1260 ms, refused online after restart and then deleted offline). Workstation
suite on `89546e0`: 257 passed, 19 skipped. A first attempt of the final Alma
run used the old upload after an SSH timeout; it was stopped, its guest was
deleted (no Snapshot was present), and the run was repeated with the checked
upload. Both hosts were left without test guests or files.

This gate used one inline review pass instead of separate review agents.
