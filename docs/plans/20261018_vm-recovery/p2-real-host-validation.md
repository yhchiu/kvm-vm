# P2 real-host validation — 2026-10-23

Status: the Snapshot lifecycle acceptance added for the review follow-up passed
on Debian 13 with AppArmor. This does not extend acceptance to other host
versions or security modes.

## Tested revision and environment

- Final code: `2505a37`, including the P1 and P2 changes through `19c68eb`.
- Host: Debian 13.6, libvirt 11.3.0, QEMU 10.0.13, AppArmor enabled.
- Python 3.13.5, pytest 8.3.5, virt-sysprep 1.52.3.
- Guest: cached Debian 12 generic cloud image, 1 vCPU, 1024 MiB RAM,
  10 GiB system disk, default libvirt NAT, dedicated temporary SSH key.
- Tests used a separate directory under `/var/lib/libvirt/images` and unique
  `e2e-snap-*` / `e2e-recovery-*` domain names. The four existing running guests
  and one existing shut-off guest remained in their original states and IDs.

Final `kvm-vm` SHA-256:
`18d135fbe5ad6b01ee1b007ce16b8a538a0e9c1002ec6cff77a4e41fe69b5e25`.

Snapshot E2E file SHA-256:
`7601210cf260aa3b1d1720fc60dda15a22fa47c91d3e711f624a942fbccc2190`.

## Failure found and fixed

The first run on `19c68eb` failed during guest creation: the newly reserved
cloud-init directory had mode `0700`, so Debian's `libvirt-qemu` could not
traverse it to open `seed.img`. Two cases failed before the run was stopped.
No existing VM was changed.

Commit `2505a37` restores mode `0755` for these exclusively created directories;
the private user-data and seed file permissions remain in place. Create and
clone regression tests now check directory traversal at the seed-generation
boundary. Both tests failed before the fix and passed afterward on POSIX.
The complete Snapshot suite was rerun using fresh test state after the fix.

## Results

| Suite | Result | Time |
| --- | --- | --- |
| Unit, Snapshot, backup-check, Restore CLI preflight, console credential regressions | 392 passed, no skips | 9.82 s |
| `tests/test_snapshot_e2e.py` | 5 passed, no skips | 2295.64 s |
| `tests/test_recovery_e2e.py` | 10 passed, no skips | 48.76 s |

The five Snapshot cases cover:

- Create/status/delete, guest data preservation, conflicting-operation refusal,
  online/offline operation and crash-consistent creation.
- Rollback of disk contents, VM configuration and identity, with guest boot.
- Successful clone, keep-storage, reinstall and delete after Snapshot deletion.
- The same lifecycle sequence after Snapshot rollback.
- The same sequence after an injected failed live deletion and successful
  offline recovery. Rollback was refused while the deletion was incomplete.

The final three cases emitted:

```text
SNAPSHOT_LIFECYCLE_PASSED delete: clone, keep-storage, reinstall, delete
SNAPSHOT_LIFECYCLE_PASSED rollback: clone, keep-storage, reinstall, delete
SNAPSHOT_LIFECYCLE_PASSED failed-delete-recovered: clone, keep-storage, reinstall, delete
```

In each lifecycle case the clone booted with the expected marker hashes,
keep-storage preserved the disk hash and managed files, reinstall booted with
a new instance ID and without the old markers, and explicit delete removed
the domain and managed resources. Fixture teardown was not counted as proof
that delete worked.

The recovery suite confirmed checkpoint/backing-chain refusal and protection
of dependent images, plus six raw/qcow2 data-disk cases covering reinstall,
delete and keep-storage. Reinstall retained the data-disk attachment; all six
cases retained the data-disk hash.

## Observed timing and AppArmor behavior

- First online freeze: 891 ms; online Snapshot deletion: 2.4 s.
- Rollback to reachable SSH: 44.1 s.
- After restarting on a Snapshot, AppArmor refused live commit to the base.
  The command reported an incomplete deletion, disabled rollback and advised
  offline deletion. That deletion succeeded and the guest data checks passed.

These are single-run measurements, not latency guarantees. The injected
failure precedes block writes; it does not repeat the earlier disk-full / partial
commit experiment. This run also does not repeat cross-host Backup Restore,
SELinux enforcing acceptance or real signal injection during reinstall.
The Restore preflight regression executes the real backup-check CLI with
synthetic backup sets and verifies that rejection stops the restore tool.

## Evidence and cleanup

Logs were downloaded and their SHA-256 values matched the remote files:

| Log | SHA-256 |
| --- | --- |
| `kvm-vm-p2-unit-final.log` | `21701a4105468375073f89910cc996e5f2099ab43b9f568963e20e843dbfd46f` |
| `kvm-vm-p2-snapshot-final.log` | `d2afd547544f64069e8d6d58ac1ce7d2f7455d24cfe735c6673f689f8dcb7861` |
| `kvm-vm-p2-recovery.log` | `ec0f45c4afbef1b678089b3b5f5b2458338f88544d9d89565df746fc24d77682` |

The initial failure log was also retained locally. After verifying that no
domain XML or process referenced the test directory, the uploaded code,
copied base image, test disks, retained failed-attempt files, temporary guest
SSH key directory and remote logs were removed. The final domain inventory
matched the pre-run inventory; no test domains remained. Host security policy
and the existing base-image cache were not changed.
