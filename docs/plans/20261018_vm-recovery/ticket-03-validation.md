# Ticket 03 validation

Status: cross-host acceptance passed on 2026-10-19 (Debian 13 to AlmaLinux 9.8,
synced-marker workload); review gate closed.

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
The destination run below completed the remaining acceptance. The committed-code
Standards and Spec review gate runs before closing the ticket.

## Destination restore, 2026-10-19

Host: AlmaLinux 9.8, Intel Xeon E5-2650 (Sandy Bridge-EP, x86-64-v2, EPT and
unrestricted guest present), libvirt 11.10.0, qemu-kvm 10.1.0 (RHEL build),
virtnbdbackup/virtnbdrestore 2.47 (EPEL) and Python 3.9.25. kvm-vm was installed
from commit `0afdabf` with `install.sh`; the default storage roots were used. The
source set was made by virtnbdbackup 2.28 on the Debian host, so this run also
covers a newer restore tool reading an older stream. The unit suite passed on
this host (199 passed, Python 3.9). The destination had no existing VM, so no
name, UUID or MAC conflict was possible. The source domain stayed shut off with
autostart disabled for the whole run.

### Transfer and isolation

- The 22-file set (1,620,848,640-byte tar, SHA-256
  `49b63287d859c91418115314e092b8fb46b1199b30f9cfdb3005cf893db70202`) was
  transferred to the destination. The tar hash matched the hash computed from the
  source stream, and `sha256sum -c SHA256SUMS` passed for all 22 files there.
- Uploading through the operator workstation was too slow and was abandoned.
  The source could not open a connection to the destination SSH port (timeout),
  while the reverse direction worked. The destination therefore pulled the tar
  from the source using a temporary key generated on the destination. The key was
  locked on the source to a forced `tar -cf - recovery-set` command with
  `restrict`. It was removed from the source `authorized_keys` and deleted on the
  destination, and the destination `known_hosts` entry for the source was removed,
  before any restore step. An earlier temporary push key was also removed from
  both hosts.
- The restore, define, boot and guest checks ran from the destination with no
  agent forwarding, no source credential and no source mount. The dedicated guest
  key was supplied separately from the set.

### Restore

`virtnbdrestore -o verify` reported checksum **1463103784**, equal to the stored
stream checksum and to the source result. `virtnbdrestore -i SET/backup -o DIR`
wrote the disk in about 8 seconds. `qemu-img info` showed one standalone qcow2
(10 GiB virtual, 1.51 GiB used, no backing file) and `qemu-img check` found no
errors. The disk was moved to `/var/lib/libvirt/images/vm/NAME.qcow2` and the
cloud files copied with `cp -a` to `/var/lib/libvirt/images/cloud-init/NAME/`;
the restored `seed.img` is byte-identical to the set copy. The effective YAML
and the JSON manifest were installed under the default definition and state
directories with only the path fields changed (`definition`, `disk`, `cloud_dir`,
`seed`). Name, `instance_id`, `managed_by`, version, provenance and creation time
were preserved. No create, clone, reinstall or cloud-init clean ran.

### Host mapping that the procedure needs

Reusing the saved domain XML unchanged fails on a RHEL-family destination:

- `virsh define` refused the saved `<emulator>/usr/bin/qemu-system-x86_64`;
  the destination binary is `/usr/libexec/qemu-kvm`.
- The saved machine type `pc-q35-10.0` is an upstream name. The RHEL build offers
  only `pc-q35-rhel9.x.0` types, so the XML used `pc-q35-rhel9.8.0`.

Besides those two edits and the two file paths, the defined XML equals the saved
one (UUID, MAC, PCI addresses, devices, CPU `host-passthrough`). The change set
was reviewed with `diff` before `virsh define`. The procedure now lists the
emulator and machine type as host mapping items. Same-family hosts would not need
them, and a helper cannot choose a compatible machine type, so no kvm-vm helper
was added: the manual reconciliation worked without a demonstrated correctness
gap.

The guest moved from a Xeon L5420 to a newer E5-2650 with `host-passthrough`.
This direction exposes more CPU features; the reverse direction is not covered.

### Destination assertions

| Check | Result |
| --- | --- |
| Define, inspect `dumpxml --inactive`, start | Passed |
| DHCP lease on the destination `default` NAT network, same MAC | Passed (192.168.122.144) |
| SSH with the dedicated guest key, `cloud-init status --wait` | Passed (`done`) |
| Marker SHA-256, machine-id, cloud-init instance-id, hostname | Identical to `guest-expected.txt` |
| All four SSH host public-key hashes | Identical |
| cloud-init did not start a new instance | Per-instance modules logged "already ran"; semaphores and the Ed25519 host key keep 2026-10-18 timestamps |
| `kvm-vm status`, `list --managed`, `validate` on the installed YAML | Managed, paths exist, validate exit 0 |
| Shut down, start, repeat identity checks | Passed; SSH reachable 14 seconds after start |
| SELinux Enforcing (temporary `setenforce 1`, then restored) | Passed: guest booted and identity matched, `svirt_t` process, no AVC records |
| QEMU guest agent | `guest-ping` returned success |
| `kvm-vm delete NAME --keep-storage --yes` | Domain undefined; disk, cloud files, YAML and JSON byte-identical (SHA-256 before and after) |
| Redefine the saved XML, `kvm-vm list --managed` | Managed again; XML identical to the pre-delete dump |

The main run was done with the host in permissive mode. The Enforcing check
repeated boot and identity verification only. The host's configuration file says
`SELINUX=disabled`; the runtime mode was returned to permissive afterward.
AppArmor was not exercised on the destination. Application consistency is not
claimed: the workload is a synced marker file and the source backup was
crash-consistent with logged freeze and thaw.

Timings: source full backup 79.87 s; destination verify about 1 s; disk restore
about 8 s; first boot to SSH within the 60-second wait; warm restart to SSH 14 s.

### Retained resources

On the destination: domain `e2e-full-f0f8bec754a9` (defined, shut off, autostart
disabled), its disk and cloud files, `/root/drill` (set, guest key and work files
with before and after artifacts) and `/root/evidence`. On the source: the original
shut-off guest, root and set. Retain them until the operator agrees the drill is
no longer needed.

### Outcome

The cross-host checks above passed; no mocked or same-host result was used. The
sealed set was held on a host other than the source. The committed-code review gate is recorded below.

## Review gate

Review of `0afdabf...44cdb5a` (documentation and evidence only; no runtime code
changed). Standards: Conventional Commit with a detailed body and no co-author
trailer, relative links resolve, terminology follows the glossary. Spec: each
ticket 03 criterion maps to evidence above. The procedure, inventory, concurrency
sequencing and consistency wording come from the earlier commits. The
cross-host restore, identity, management and delete/redefine checks are in the
destination tables. The helper question is answered with a recorded reason (no
demonstrated correctness gap). No must-fix findings. This gate used one inline
review pass instead of separate review agents because the change set has no code.
