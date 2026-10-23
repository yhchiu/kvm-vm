# Review follow-up validation — 2026-10-23

The six remaining recommended changes were implemented and tested:

| Commit | Change |
| --- | --- |
| `446c73b` | Newly owned storage and cloud-init directories remain traversable under umask 077; existing directory and private file permissions are preserved |
| `f68730b` | Real SIGINT/SIGTERM integration tests at four reinstall boundaries, targeting the process or its group |
| `9dd173e` | E2E command groups stop before cleanup; remaining locks and uncertain domain inventory retain resources |
| `3b4e48d` | Correct Snapshot record documentation and describe interruption validation |
| `05bc5cf` | Malformed Snapshot field types become unreadable records with manual recovery guidance |
| `da9b707` | Shared backing path, domain file-source and acceptance hash helpers |

## Local validation

- WSL Debian complete suite: **474 passed, 33 skipped** in 32.93 seconds.
  Missing real-KVM and other prerequisites remain skips, not acceptance evidence.
- Windows Snapshot suite after record validation: **82 passed** in 13.22 seconds.
- POSIX coverage includes create, clone and reinstall with umask 077,
  16 actual-signal cases, and timeout/cancellation of stubborn child processes.
- Snapshot status, delete and rollback are exercised against 18 malformed
  records; disk files, records and libvirt snapshot inventory stay unchanged.

## Real-host acceptance

Tested revision: `da9b707`, on the previously authorized Debian 13.6 host,
libvirt 11.3.0, QEMU 10.0.13, AppArmor enabled. Python 3.13.5 and pytest 8.3.5.
The guest used the existing Debian 12 cloud image, 1 vCPU, 1024 MiB RAM,
10 GiB system disk, default NAT and a temporary dedicated SSH key.

`kvm-vm` SHA-256:
`659c99a0fbb8b5b6c940b7f658bf25ebcfd6779b814dcb5997e8fe2a3bfabee0`.

| Run | Result |
| --- | --- |
| Unit, Snapshot, backup check, Restore preflight, console password, real signals and process cleanup | **449 passed**, 18.71 seconds |
| `tests/test_recovery_e2e.py` | **10 passed**, 57.32 seconds |
| `test_lifecycle_after_snapshot_recovery[delete]`, with inherited **umask 077** | **1 passed**, 565.75 seconds |

The strict-umask run booted a guest, created and deleted its Snapshot,
verified guest data, cloned and booted it, verified the clone's data, deleted
the clone, checked unchanged disk and management-file hashes after
`delete --keep-storage`, then reinstalled, booted and deleted the source.
Reinstall verification checked a new instance ID and absence of old markers.
Filesystem freeze lasted 864 ms. Storage/cloud directories had mode 0755;
cloud-init user-data remained 0600.

All tests used isolated directories and uniquely named domains. The four
existing running guests retained their original IDs and states; the existing
shut-off guest remained shut off. Test domains, lifecycle locks and VM artifacts
were absent after fixture cleanup. After logs were downloaded, all task-owned
remote code, cached image copy, pytest directories, logs and upload files were
removed after verifying no domain XML or process referenced the test root.

## Evidence and limits

Logs are retained locally under `C:/Users/Arthur/AppData/Local/Temp/`, with
prefix `kvm-vm-review-da9b707-`:

| Suffix | SHA-256 |
| --- | --- |
| `unit.log` | `51c1a9b80d173f5f9237626e1c8b7e7721864dc4e49111a95b9991c8273b3672` |
| `recovery.log` | `9177f278ea7cc977ada18ca2f92f5f32cb78e953b32ba9b4fb1f1ca1c1258137` |
| `snapshot.log` | `346d4962a2877fbf795fd773c37dcff7a66bb1c494451920dde99482a4a906fe` |
| `domains-final.log` | `74df1de0b7004a69fdbd2bf89ba585edcdcf542a1cab2bbcdbbbad5754784f90` |

The signal integration cases use simulated host commands and a real blocking
subprocess; they do not prove real-libvirt behavior at each interrupted phase.
This run does not repeat every Snapshot E2E outcome from the earlier
[P2 acceptance](p2-real-host-validation.md). SELinux enforcing, cross-host
complete Backup Restore and power-loss behavior remain outside this evidence.
