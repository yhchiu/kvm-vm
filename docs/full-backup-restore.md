# Full Backup and replacement-host Restore

Status: validated on 2026-10-19 by the [two-host exercise](full-backup-acceptance.md)
(Debian 13 source, AlmaLinux 9.8 destination, one Debian 12 guest with a synced
marker workload): a full Backup in the
[ticket 03 record](plans/20261018_vm-recovery/ticket-03-validation.md), and a
full plus incremental history, chosen recovery points and the next Backup
history in the [ticket 04 record](plans/20261018_vm-recovery/ticket-04-validation.md).
That is one tested path, not a guarantee for other guests, applications,
firmware modes or host combinations.

A Snapshot is a short-lived local rollback point. A Backup recovery set must
survive loss of the original host and storage. This procedure uses
`virtnbdbackup` and `virtnbdrestore` directly; kvm-vm adds no backup engine or
wrapper command.

## Initial validation profile

Use Linux/KVM, `qemu:///system`, one persistent x86-64 BIOS VM, one standalone
qcow2 v3 disk, a cloud-init seed CD-ROM, and one libvirt NAT network. The source
must be running for the full-backup example. Use compatible CPU features,
machine type, emulator, virtual devices and network addressing on the target.
The source candidate is Debian 13, libvirt 11.3.0, QEMU 10.0.13 and Debian's
virtnbdbackup 2.28-1. Record actual versions on both hosts before testing;
compatibility is not established by matching version strings alone.

The tested destination is AlmaLinux 9.8 with libvirt 11.10.0, QEMU 10.1.0 and
virtnbdbackup 2.47 from EPEL; it restored a set written by version 2.28.
Install the distribution's (or EPEL's) virtnbdbackup package on both hosts,
including its libvirt/libnbd Python dependencies. Existing kvm-vm operations do not require
these optional tools. The destination also needs kvm-vm and its normal runtime
dependencies. Do not install the unrelated PyPI package named `nbd`.

Confirm that the NBD socket and scratch paths are permitted by the host's
security policy before capture. On the Debian validation host, AppArmor denied
the default `/var/tmp/virtnbdbackup.PID` socket. Passing `-f SOCKET` inside the
specific guest's already-permitted libvirt runtime directory allowed backup
without changing AppArmor. Inspect the guest's generated profile for its actual
runtime directory and choose an unused filename; the runtime ID/path changes
after each boot. Do not reuse another domain's directory, edit generated
profiles or disable AppArmor. Where no suitable path exists, have the host
administrator prepare a narrowly scoped policy before continuing. Add the
validated `-f SOCKET` argument to the backup command below as required.

This initial procedure excludes UEFI/NVRAM, Secure Boot, TPM, encrypted disks or
libvirt secrets, host-device passthrough, external kernel/initrd, shared storage,
extra writable disks, backing chains, external data files, snapshots and managed
save/RAM state. Check actual domain XML and disk metadata, not only the YAML.
If any exclusion applies, stop: the inventory below is incomplete for that VM.
UEFI and TPM support would require preserving and validating their independent
mutable state before broadening the profile.

## Recovery-set inventory

| Artifact | Purpose and source |
| --- | --- |
| Entire external-tool output directory | Disk streams, checksums, checkpoint descriptions, saved XML and logs; keep the set together |
| Persistent domain XML, plus live XML for comparison | UUID, MAC, devices, CPU and machine type; `virsh dumpxml --inactive` and `dumpxml` |
| Effective YAML | The file named by the state manifest's `definition`, normally `/etc/kvm-vm/definitions/NAME.yaml` |
| JSON state manifest | `/var/lib/kvm-vm/state/NAME.json`, including instance ID and management paths |
| Complete cloud-init directory and actual seed | Normally `/var/lib/libvirt/images/cloud-init/NAME/`; include the manifest's `cloud_dir` and `seed`, and verify the XML references that seed |
| Host mapping notes | Storage roots, environment overrides, libvirt URI, network name/XML, bridge/subnet/DHCP requirements, permissions and autostart policy |
| Recovery-point notes | Start/end UTC, versions, application consistency method, test identity/data and artifact checksums |

The original input YAML and cached OS image are provenance, not substitutes for
the effective YAML or backed-up disk. `definition_source` and `source` in JSON
can retain historical provenance; Restore must not fetch their original paths.
Future reinstall requires an available OS source separately. Include the
kvm-vm release or commit identifier and obtain that executable independently.
Protect the recovery set as sensitive data: disk and cloud-init contents may
contain credentials. Store it on independently retained storage with suitable
access control and encryption under the operator's storage policy.

## Capture one coherent recovery point

Reserve an exclusive maintenance window from inventory through sealing the
set. Disable scheduled backup/configuration jobs and exclude concurrent
kvm-vm, virsh, migration, block jobs, disk resize, snapshot and cloud-init edits.
The kvm-vm lifecycle lock does not coordinate external backup tools. A manual
window must cover all operators and schedulers; a before/after comparison alone
cannot rule out an intermediate change. Guest application writes may continue
only within the chosen consistency policy.

Filesystem freezing and application consistency are different. Confirm a
working QEMU guest-agent channel if relying on filesystem quiescing, inspect
backup logs for freeze/thaw failures, and confirm the guest is thawed afterward.
A database may additionally require its own supported backup/quiesce procedure.
Without quiescing, classify an online backup as crash-consistent and test that
workload's recovery. Do not label a warning-only run application-consistent.

These Bash examples assume the default management paths and `qemu:///system`.
Set `VM` to the exact domain and `SET` to a new, empty recovery-set location.
For environment overrides or relocated artifacts, substitute the inspected
manifest paths before copying. Abort on any command failure.

```bash
set -euo pipefail
export LC_ALL=C
VM=example-vm
SET=/mnt/independent-backups/example-vm-full-20261019
test ! -e "$SET"
umask 077
mkdir -p "$SET/management/cloud"
virsh -c qemu:///system dumpxml "$VM" --inactive > "$SET/management/domain.xml"
virsh -c qemu:///system dumpxml "$VM" > "$SET/management/live.xml"
virsh -c qemu:///system dominfo "$VM" > "$SET/management/dominfo.txt"
cp -a "/var/lib/kvm-vm/state/$VM.json" "$SET/management/state.json"
cp -a "/etc/kvm-vm/definitions/$VM.yaml" "$SET/management/effective.yaml"
cp -a "/var/lib/libvirt/images/cloud-init/$VM/." "$SET/management/cloud/"
date -u +%FT%TZ > "$SET/started.txt"
virtnbdbackup -d "$VM" -l full -o "$SET/backup"
date -u +%FT%TZ > "$SET/finished.txt"
virtnbdrestore -i "$SET/backup" -o verify
virtnbdrestore -i "$SET/backup" -o dump > "$SET/backup-info.txt"
virsh -c qemu:///system dumpxml "$VM" --inactive > "$SET/management/domain-after.xml"
cmp "$SET/management/domain.xml" "$SET/management/domain-after.xml"
cmp "/var/lib/kvm-vm/state/$VM.json" "$SET/management/state.json"
cmp "/etc/kvm-vm/definitions/$VM.yaml" "$SET/management/effective.yaml"
diff -r "/var/lib/libvirt/images/cloud-init/$VM" "$SET/management/cloud"
```

Before running the backup, inspect `domblklist --details`, both XML files and
`qemu-img info --output=json --force-share DISK`. Require exactly the supported
disk and seed topology, no snapshots, existing backup checkpoints or active
block job. Live and persistent configuration must agree on recovery-relevant
devices and identity; runtime-only XML fields need not be identical. Save the
network XML with `virsh net-dumpxml NETWORK`, tool versions and inspected disk
metadata inside `management/`. Review the backup log and dump output: exactly
the intended disk must be present. The CD-ROM seed is covered by the separate
cloud directory copy. Record any guest-agent warning and consistency limitation.

Add your recovery-point notes before sealing. With no further writers to SET:

```bash
(cd "$SET" && find . -type f ! -path ./SHA256SUMS -print0 |
    sort -z | xargs -0 sha256sum > SHA256SUMS)
(cd "$SET" && sha256sum -c SHA256SUMS)
```

Copy the entire sealed set to independent storage if it was staged locally;
repeat checksum and tool verification at the retained location. A local staging
directory alone is not disaster protection. Keep failed or unsealed runs clearly
separate from usable recovery points. Starting a new full backup can retire the
tool's old tracking baseline; never run it casually against an unrelated chain.

## Restore on the replacement host

Fence the source VM before starting its preserved identity elsewhere. In a drill,
shut it down, disable autostart and keep it off. In a disaster, use the host/storage
fencing procedure so the original cannot unexpectedly rejoin. Do not run two
copies with the same UUID, MAC, IP or guest identity on connected networks.

Use a distinct target with no access to source disks, original configuration
directories, source mounts or source-host credentials. The retained recovery set
must be the only source of VM-specific recovery artifacts. Check name/UUID/MAC
and every destination path for conflicts; abort rather than overwrite anything.
Prepare the compatible network and storage first. Saved network XML is an input
to review, not permission to replace an existing host network. Static addressing
must remain valid; preserve the seed bytes and instance ID.

```bash
set -euo pipefail
export LC_ALL=C
SET=/mnt/recovery/example-vm-full-20261019
RESTORED=/var/lib/libvirt/images/recovered-example-vm
test ! -e "$RESTORED"
(cd "$SET" && sha256sum -c SHA256SUMS)
kvm-vm backup check "$SET/backup"
virtnbdrestore -i "$SET/backup" -o verify
virtnbdrestore -i "$SET/backup" -o "$RESTORED"
```

`kvm-vm backup check` (a kvm-vm release that includes it, see
[ADR 0002](adr/0002-pre-restore-backup-check.md)) refuses a Backup directory
that `virtnbdrestore` would restore incompletely or in the wrong order while
still exiting 0; see [restore a chosen point](#restore-a-chosen-point). It reads
the directory only and needs no root or backup tools.

Do not pass `-c` or `-D`: keep control of identity and definition. Inspect the
actual output disk filename and `qemu-img info`/`qemu-img check` while offline.
Require one standalone qcow2 with no external dependency. Prepare working
copies of the saved persistent XML and JSON, leaving the sealed set untouched.

| Destination item | Required reconciliation |
| --- | --- |
| Domain XML | Set the writable disk's `source file` to the reconstructed disk and the CD-ROM source to the restored seed; retain name, UUID, MAC and device configuration |
| Domain XML `<emulator>` and machine type | Set both to values the destination offers (`virsh capabilities`). RHEL-family hosts use `/usr/libexec/qemu-kvm` and `pc-q35-rhel9.x.0`, not the upstream `/usr/bin/qemu-system-x86_64` and `pc-q35-10.0` saved from a Debian host. Review the resulting `diff` against the saved XML; a changed machine type may move device addresses |
| JSON `disk` | Exact same reconstructed disk path as XML |
| JSON `definition` | Installed effective YAML path |
| JSON `cloud_dir`, `seed` | Restored cloud directory and seed path, consistent with XML |
| JSON identity | Preserve `name`, `instance_id`, `managed_by`, version and provenance |
| Effective YAML | Preserve guest identity and provisioning settings; if host network mapping must change, keep YAML and XML consistent and document the mapping |
| kvm-vm environment | Set `KVM_VM_DISK_DIR`, `KVM_VM_CLOUD_DIR`, `KVM_VM_STATE_DIR`, `KVM_VM_DEF_DIR` to the intended roots on every invocation |

Keep the disk under `KVM_VM_DISK_DIR` and cloud directory under
`KVM_VM_CLOUD_DIR` so later lifecycle commands can manage them. Install the JSON
as `STATE_DIR/NAME.json`, YAML as `DEF_DIR/NAME.yaml`, and cloud files without
regenerating them. No create, clone, reinstall, sysprep or cloud-init clean is
part of Restore. Inspect XML for every remaining source-host path; only
compatible destination host resources or restored artifacts may remain.

Give libvirt/QEMU the required directory traversal and image access using the
destination distribution's service account and labeling policy. Do not use
world-writable permissions; preserve restrictive management-file permissions.
Check SELinux/AppArmor path policy where enabled. Define the reviewed XML with
`virsh -c qemu:///system define RESTORED-DOMAIN.xml`, inspect it again with
`dumpxml --inactive`, then start the VM. Restore its recorded autostart policy
only after identity isolation and successful verification.

Do not import old checkpoint XML as a usable incremental baseline. Restored
disks establish a new history; see
[start the next Backup history](#start-the-next-backup-history).

Verify guest SSH, application/data recovery, UUID/MAC, machine ID, SSH host keys
and cloud-init instance ID against captured expectations. Run kvm-vm `status`,
`list --managed` and `validate` against the installed YAML. For a disposable
drill, follow the management exercise linked above. Checksums and a successful
restore process do not establish a bootable, managed recovery.

## Incremental Backup history

### Checkpoints are tracking, not recoverable data

A libvirt checkpoint is a name, libvirt metadata on the source host and a
persistent dirty bitmap inside the qcow2 disk. It only tells the next
`virtnbdbackup -l inc` which blocks changed. It holds no guest data and is lost
with the host. The recoverable data is the Backup directory itself:

| File | Meaning |
| --- | --- |
| `NAME.cpt` | JSON list of the history, `virtnbdbackup.0` (the full Backup) to `virtnbdbackup.N` |
| `vda.full.data`, `vda.inc.virtnbdbackup.N.data` | Disk data; point N needs the full file and every increment 1 to N |
| `*.data.chksum` | Stream checksums used by `virtnbdrestore -o verify` |
| `checkpoints/virtnbdbackup.N.xml` | Checkpoint descriptions with parent links |
| `vmconfig.virtnbdbackup.N.xml` | The *live* domain XML at Backup time, not the persistent definition |

Losing checkpoint metadata or bitmaps does not lose data that is already in the
set. It only stops that history from continuing.

### Capture each recovery point

Keep one Backup directory per history inside the recovery set. The full Backup
creates point 0 and each incremental Backup adds the next point. Before every
Backup, capture the management artifacts into `points/N/`, exactly as in
[capture one coherent recovery point](#capture-one-coherent-recovery-point),
and record the guest expectations for that point:

```bash
set -euo pipefail
export LC_ALL=C
VM=example-vm
SET=/mnt/independent-backups/example-vm-history-20261019
N=1            # 0 with LEVEL=full for a new history
LEVEL=inc
P="$SET/points/$N"
test ! -e "$P"
umask 077
mkdir -p "$P/management/cloud"
virsh -c qemu:///system dumpxml "$VM" --inactive > "$P/management/domain.xml"
virsh -c qemu:///system dumpxml "$VM" > "$P/management/live.xml"
cp -a "/var/lib/kvm-vm/state/$VM.json" "$P/management/state.json"
cp -a "/etc/kvm-vm/definitions/$VM.yaml" "$P/management/effective.yaml"
cp -a "/var/lib/libvirt/images/cloud-init/$VM/." "$P/management/cloud/"
virtnbdbackup -d "$VM" -l "$LEVEL" -o "$SET/backup"
python3 -c 'import json, sys; assert json.load(open(sys.argv[1]))[-1] == sys.argv[2]' \
    "$SET/backup/$VM.cpt" "virtnbdbackup.$N"
virsh -c qemu:///system dumpxml "$VM" --inactive | cmp - "$P/management/domain.xml"
virtnbdrestore -i "$SET/backup" -o verify
```

Then reseal the whole set as in the full procedure and copy it to the retained
storage. Pair each disk point with its own `points/N/management/` when you
restore. Do not use the latest management files with an earlier point, and do
not use `vmconfig.virtnbdbackup.N.xml` for configuration: in the validation run
the persistent definition changed from 1 vCPU/1024 MiB to 2 vCPU/1536 MiB before
point 2, yet all three `vmconfig` files still showed the live 1 vCPU/1024 MiB.

Copy the set with a method that keeps file modification times, such as `tar`,
`cp -a` or `rsync -a`. `virtnbdrestore` 2.28 and 2.47 sort the data and
`vmconfig` files by modification time, not by checkpoint name, so a copy that
renews one file's time changes which data is restored.

### Restore a chosen point

```bash
set -euo pipefail
SET=/mnt/recovery/example-vm-history-20261019
N=1
RESTORED=/var/lib/libvirt/images/recovered-example-vm
test ! -e "$RESTORED"
(cd "$SET" && sha256sum -c SHA256SUMS)
kvm-vm backup check "$SET/backup" --until "virtnbdbackup.$N"
virtnbdrestore -i "$SET/backup" -o verify
virtnbdrestore -i "$SET/backup" -o "$RESTORED" --until "virtnbdbackup.$N"
```

Continue with the replacement-host reconciliation above using
`points/N/management/`, and verify the guest against `points/N` expectations.
All three checks before the restore are required, because the tool alone reports
success for several broken sets:

| Broken set | `virtnbdrestore` 2.28 | `virtnbdrestore` 2.47 | Refused by |
| --- | --- | --- | --- |
| Increment before the chosen point missing | `verify` 0, restore 0 | `verify` 0, restore 1 with a Python traceback | SHA256SUMS, `kvm-vm backup check` |
| Chosen point not in the history | restore 0 | restore 1 with a Python traceback | `kvm-vm backup check` |
| `NAME.cpt` missing | `verify` 0, restore 0 | `verify` 0, restore 0 | SHA256SUMS, `kvm-vm backup check` |
| Increment corrupted | `verify` 1, restore 0 | `verify` 1, restore 0 | SHA256SUMS, `verify` |
| Increment newer than a later increment (timestamp only) | Same sort by time in its code; not run | `verify` 0, restore 0, wrong disk | `kvm-vm backup check` only |

In the last case the bytes and SHA256SUMS are intact. With `--until
virtnbdbackup.2`, version 2.47 applied the full Backup and increment 2, then
stopped without applying increment 1. The guest booted with a corrupted
`point-1.bin`. `kvm-vm backup check` refuses the set and names the order. To repair
it, copy the set again with times preserved. Or, on a working copy whose
SHA256SUMS still pass, set the times in checkpoint order with `touch -d`; that
copy restored a disk identical to the original set's.

### Start the next Backup history

A restored disk has no bitmaps and the restored domain has no checkpoints, so
the old history cannot continue. An incremental Backup from the restored guest
into a copy of the old directory failed with `checkpoint inconsistent: missing
or broken bitmap` (exit 1). It wrote no data and left no checkpoint. Do not edit
`NAME.cpt` or define old checkpoint XML to force it. Keep the old set as
historical recovery points and start a new history in a new directory with
`-l full`, capturing point 0 as above. Later incremental Backups then work, and
a restore of the new history returned the changes made after the restore.

On the original host, the same rules apply:

- A guest restart keeps the bitmaps; an incremental Backup after a restart
  continued the history.
- A new `-l full` into a new directory retires the old libvirt checkpoints and
  leaves only `virtnbdbackup.0`. An incremental into the old directory is then
  refused (exit 1) without writing data.
- Deleting only checkpoint metadata (`checkpoint-delete --metadata`) leaves the
  bitmap. The next incremental then fails with `Bitmap already exists`. Retire a
  history while the guest runs with
  `virsh checkpoint-delete VM virtnbdbackup.0 --children`, which removes the
  checkpoints and their bitmaps. libvirt 11.10 refuses this for a shut-off
  domain (`cannot delete checkpoint for inactive domain`).
- kvm-vm `clone`, `reinstall` and `delete` refuse a VM with checkpoints until
  the history is retired.
- kvm-vm Snapshots are never combined with checkpoints: retire the tracking
  before `kvm-vm snapshot create`, and start a new full Backup after deleting
  or rolling back the Snapshot. See
  [Snapshots and Backups](snapshots.md#snapshots-and-backups).

## References

- [virtnbdbackup upstream usage](https://github.com/abbbi/virtnbdbackup#readme):
  full/copy modes, integrity verification, restore and configuration options.
- [libvirt domain XML](https://libvirt.org/formatdomain.html): host/device paths,
  identity and firmware state.
- [Lifecycle recovery safety](lifecycle-recovery-safety.md): supported topology,
  checkpoint refusals and management ownership boundaries.
