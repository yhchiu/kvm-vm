# Snapshots

A Snapshot is one short-lived local rollback point per VM, taken before an
update or configuration change. It is not a Backup: it lives on the same host
and storage as the VM. Delete it once the change has succeeded. Rollback is
added separately; until then a Snapshot can be created, inspected and deleted.

```sh
kvm-vm snapshot create web01            # running or shut off VM; no reboot
kvm-vm snapshot status web01            # none, ready or incomplete
kvm-vm snapshot delete web01 --yes      # keep the current data, remove the Snapshot
```

Validation: see the [ticket 05 record](plans/20261018_vm-recovery/ticket-05-validation.md).

## How it works

kvm-vm asks libvirt for an external disk-only snapshot. The VM's disk,
`NAME.qcow2`, becomes the recovery point and stays read-only. New guest writes
go to an overlay, `NAME.kvm-vm-<UTC time>.qcow2`, in the same directory.
`kvm-vm snapshot delete` merges the overlay back into `NAME.qcow2` and removes
it, so the disk is again one standalone file:

- For a running VM, libvirt commits the overlay live and switches the VM back
  to `NAME.qcow2` without a restart.
- For a shut off VM, kvm-vm runs `qemu-img commit` itself, points the domain
  definition back to `NAME.qcow2` and removes libvirt's snapshot metadata. It
  does not use libvirt's offline deletion, because on AppArmor hosts that runs
  inside a confined QEMU that may not write the recovery-point disk (see
  [AppArmor hosts](#apparmor-hosts)).

kvm-vm also keeps a record in `KVM_VM_STATE_DIR/snapshots/NAME/`:

| File | Content |
| --- | --- |
| `record.json` | Snapshot name, recovery-point and overlay paths, status (`creating`, `ready`, `deleting`), consistency and measured freeze time |
| `domain.xml`, `live.xml` | Persistent and (for a running VM) live domain XML at creation |
| `effective.yaml`, `state.json`, `cloud/` | kvm-vm definition, state manifest and cloud-init files at creation |

The record is written before anything changes and is removed only after a
successful delete, so an interrupted operation is always visible. libvirt also
stores the domain configuration in its snapshot metadata. RAM and running
processes are not captured.

## Supported configuration

Creation checks all of these before it changes anything, and refuses otherwise:

- libvirt daemon 9.9 or newer through the QEMU driver (`qemu:///system`).
  Tested: AlmaLinux 9.8 (libvirt 11.10.0, QEMU 10.1.0, SELinux permissive) and
  Debian 13 (libvirt 11.3.0, QEMU 10.0.13, AppArmor), with Debian 12 and
  AlmaLinux 9 guests.
- A VM managed by kvm-vm (state manifest and effective definition present).
- The VM is running or shut off (not paused).
- Exactly one system disk: a regular, standalone qcow2 file whose live,
  persistent and managed paths agree. No backing file, internal snapshot,
  persistent bitmap or external data file. CD-ROMs such as the cloud-init seed
  are excluded from the Snapshot; floppy and other disk devices are refused.
- BIOS boot: UEFI firmware, NVRAM and TPM state are not captured, so these VMs
  are refused.
- No Backup checkpoints (see [Backups](#backups)), no other libvirt snapshot,
  and no active domain or block job.

## Consistency and pause

For a running VM, kvm-vm freezes the guest filesystems through the QEMU guest
agent, creates the snapshot and thaws them. kvm-vm installs and starts
`qemu-guest-agent` by default. If the agent does not respond, creation is
refused. Pass `--no-quiesce` to take a crash-consistent Snapshot instead; the
record and output say so. A filesystem freeze is not application consistency:
a database may still need its own procedure. A shut off VM needs no freeze.

The output and `kvm-vm snapshot status` show how long the filesystems were
frozen. Measured examples are in the validation record; they depend on the
guest and host and are not a guaranteed limit.

## Inspect

`kvm-vm snapshot status NAME` exits 0 when there is no Snapshot or a usable
one, and 1 when something needs attention:

- **none**: no Snapshot.
- **ready**: the record, the libvirt snapshot and both domain XMLs agree on
  the overlay and the recovery point.
- **incomplete**: an operation was interrupted, failed part way, or the
  Snapshot was changed outside kvm-vm. The output lists each problem and what
  to do.
- **other libvirt snapshots**: snapshots that kvm-vm did not create. kvm-vm
  does not delete or roll them back, and refuses to create one of its own.

`kvm-vm status NAME` includes the same summary.

## Failures and interruptions

| Situation | Result | What to do |
| --- | --- | --- |
| Agent missing, freeze fails, or libvirt refuses the snapshot | Guest thawed, record removed, disk unchanged | Fix the cause or use `--no-quiesce` |
| Thaw fails after the snapshot was created | Snapshot kept as ready with a warning; command fails | Run `virsh -c qemu:///system domfsthaw NAME` at once |
| Creation interrupted (Ctrl-C) | Guest thawed. If libvirt had not started, nothing is left; otherwise the record stays `creating` | `kvm-vm snapshot status`, then `kvm-vm snapshot delete NAME --yes` to finish or discard it |
| kvm-vm killed while the guest was frozen | Record shows the freeze without a thaw | Thaw with `virsh domfsthaw`, then as above |
| Deletion interrupted | Record stays `deleting` | Run `kvm-vm snapshot delete NAME --yes` again |
| libvirt refuses to delete for a running VM | Snapshot left unchanged and `ready` | Shut the VM down and delete again |
| Snapshot changed with virsh or another tool | Status reports the mismatch; delete refuses | Inspect `virsh snapshot-list` and the domain disks by hand |

`kvm-vm snapshot delete` removes a leftover overlay only when no domain and no
image in `KVM_VM_DISK_DIR` refers to it; otherwise it keeps the file and warns.

## Working with other commands and tools

`snapshot create` and `snapshot delete` take the same lifecycle lock as
`create`, `clone`, `reinstall` and `delete`. `clone`, `reinstall` and `delete`
(including `--keep-storage`) refuse a VM that has a Snapshot or an incomplete
Snapshot operation. Delete the Snapshot first; that keeps the current data.

Direct tools do not take the lock. While kvm-vm creates or deletes a Snapshot,
do not run `virsh snapshot-*`, `blockcommit`, `blockcopy`, `blockpull`,
migration or a Backup job on that VM. While a Snapshot exists, do not create
other libvirt snapshots and do not start Backups with checkpoints.

The overlay grows with every guest write until the Snapshot is deleted, and
deleting copies those writes into the original disk. Keep enough free space in
the disk directory, and keep Snapshots short-lived.

## AppArmor hosts

On Debian 13 (libvirt 11.3), when QEMU starts with a Snapshot already in place
(a VM shut down and started again, a host reboot, or a Snapshot of a shut off
VM), libvirt's generated AppArmor profile contains `deny "<recovery-point disk>"
w`. libvirt then cannot commit the overlay while the VM runs, and its own
offline deletion fails the same way ([libvirt issue
#806](https://gitlab.com/libvirt/libvirt/-/issues/806)). The rule is silent:
no AppArmor denial is logged.

kvm-vm handles this without editing profiles or disabling AppArmor. If the
running VM's QEMU was started before the Snapshot existed, the live delete
works. Otherwise `kvm-vm snapshot delete` reports libvirt's error, leaves the
Snapshot unchanged and asks you to shut the VM down; the offline delete with
`qemu-img commit` then works.

## Backups

A Snapshot does not protect against losing the host or its storage; use the
[Backup procedure](full-backup-restore.md) for that. Snapshot creation refuses
a VM with Backup checkpoints, because Snapshots together with incremental Backup
tracking have not been verified yet. The checkpoints and Backups are left
unchanged. Do not start a Backup while a Snapshot exists.
