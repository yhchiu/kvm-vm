# Ticket 03 validation

Status: in progress; cross-host acceptance has not passed.

Implementation/review fixed point: `4a4b189e278db9a9bccd139830e7e8fccfaa76df`.

## Scope and prerequisites

- [Recovery procedure](../../full-backup-restore.md).
- [Operator-run two-host acceptance](../../full-backup-acceptance.md).
- `tests/prepare_full_backup.py` runs only the source CLI/guest-SSH preparation.
  It intentionally retains its isolated guest and backup for target validation.
- No kvm-vm runtime helper was added: no demonstrated metadata workflow gap yet
  justifies a product command. Manual reconciliation remains to be exercised.
- A second compatible host and authorization to transfer the test recovery set
  are still missing. Do not advance ticket 04 or close the review gate.

## Source environment, 2026-10-19

The authorized source host runs Debian 13, libvirt 11.3.0, QEMU 10.0.13 and
Python 3.13.5. The distribution package simulation proposed only additions:
24 new packages, no upgrades or removals. Installed virtnbdbackup 2.28-1 and
dependencies including python3-libnbd 1.22.2-1+b1. Both backup/restore commands
report 2.28. Installed CLI help confirms the documented `-o verify`, `-o dump`
and explicit restore-output modes. Existing unrelated domains were not operated
on and kvm-vm was not installed over the host's system executable.

The test uses the isolated uploaded executable based on ticket 02, Debian 12,
one vCPU, 1024 MiB RAM, a 10 GiB standalone qcow2 disk and the default NAT
network. It generates its own guest SSH key and unique VM name. The recovery
set captures actual domain XML, effective YAML, JSON, cloud files, guest marker
and identity expectations. It is staged locally until a destination is supplied;
local staging does not meet independent-retention acceptance.

The existing unit suite passed: **199 passed in 2.06 seconds**. No runtime code
changed in this ticket. Documentation Bash blocks passed `bash -n`; relative
procedure links and `git diff --check` passed. Target boot/data/identity/management
results remain pending.

## Real source preparation

The first run, `e2e-full-f7f7847dde06`, reached an online full backup but failed
to bind the default NBD socket. The kernel recorded an AppArmor denial for that
domain's profile. The backup tool thawed the guest; inspection showed no
checkpoint and no active job. A diagnostic retry using an unused socket in the
domain's already-permitted runtime directory succeeded. No security policy was
changed or disabled. This diagnostic guest is retained shut off with autostart
disabled; its original incomplete set is not the acceptance set.

The final script was then run from scratch with:

```bash
python3 -u tests/prepare_full_backup.py --socket-template \
  '/var/lib/libvirt/qemu/domain-{id}-{name20}/recovery-nbd.sock'
```

It exited **0** and reported **SOURCE PREPARATION PASSED** for
`e2e-full-f0f8bec754a9`. Results:

- Full backup completed in **79.87 seconds**, saving approximately 1.5 GiB from
  a 10 GiB virtual disk. Both filesystem freeze and thaw were logged during
  `22:38:44` in the host log's timezone; second-resolution timestamps are not a
  measured pause latency. This is a synced-marker workload, not a database test.
- The guest remained running and reachable by SSH after backup. Marker hash,
  machine ID, cloud-init instance ID, hostname and SSH public-key hashes matched
  their before-backup values.
- Persistent XML, effective YAML, JSON state and all cloud-init files were
  unchanged across capture. `kvm-vm status` reported the managed VM correctly.
- `virtnbdrestore -o verify` reported checksum **1463103784**, matching the
  stored stream checksum. SHA-256 verification passed for all 22 recovery-set
  files. This verifies the saved bytes, not a recovered boot.
- The script then shut down the source normally and disabled autostart. A
  separate `virsh dominfo` confirmed both conditions.

Retained source paths (do not remove before destination testing):

- Root: `/var/lib/libvirt/images/e2e-full-f0f8bec754a9`
- Recovery set: `ROOT/recovery-set` (still source-local)
- Dedicated test guest key: `ROOT/guest-key` (outside the recovery set)
- Expected guest results: `ROOT/recovery-set/management/guest-expected.txt`
- Domain UUID: `337ebcda-4fa6-4e31-b207-94d5293bcf75`
- MAC: `52:54:00:ed:63:03`
- Cloud-init instance ID: `iid-7630c4c7e414d3ca5a00aa51`

The source-only preparation is committed separately so it can be resumed.
Independent retention, destination mapping/boot, identity and management
acceptance remain unverified. Ticket 03 stays open; its committed-code Standards
and Spec review gate will run when the full ticket is ready for closure.
