# Snapshots

A Snapshot is one short-lived local rollback point per VM, taken before an
update or configuration change. It is not a Backup: it lives on the same host
and storage as the VM. Delete it once the change has succeeded, or roll back to
it if the change went wrong.

```sh
kvm-vm snapshot create web01                     # running or shut off VM; no reboot
kvm-vm snapshot status web01                     # none, ready or incomplete
kvm-vm snapshot delete web01 --yes               # keep the current data, remove the Snapshot
kvm-vm snapshot rollback web01 --yes --force     # discard the changes, return to it, start again
```

Validation: see the [ticket 05](plans/20261018_vm-recovery/ticket-05-validation.md)
and [ticket 06](plans/20261018_vm-recovery/ticket-06-validation.md) records.

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
successful delete or rollback, so an interrupted operation is always visible.
libvirt also stores the domain configuration in its snapshot metadata. RAM and
running processes are not captured.

## Tested host configurations

Snapshot acceptance is limited to these recorded host combinations. It does
not establish support for every version above a minimum or for other security
modes. These are historical results for the revisions in the linked records,
not evidence that every later revision has been rerun on those hosts.

| Host | libvirt | QEMU | Host security mode | Evidence and limits |
| --- | --- | --- | --- | --- |
| AlmaLinux 9.8 | 11.10.0 | 10.1.0 | SELinux permissive | [Snapshot failure validation](plans/20261018_vm-recovery/snapshot-simplification-validation.md); enforcing mode was not tested |
| Debian 13 | 11.3.0 | 10.0.13 | AppArmor | [Snapshot failure validation](plans/20261018_vm-recovery/snapshot-simplification-validation.md); live deletion after reboot can be refused, requiring offline deletion |

Guest coverage includes Debian 12 and AlmaLinux 9; see the
[Snapshot/Backup interoperability record](plans/20261018_vm-recovery/ticket-07-validation.md)
for the actual combinations. Other libvirt/QEMU combinations, including
libvirt 9.9 through 11.2, and SELinux enforcing are **unvalidated**. The CLI
does not detect or certify a host's QEMU/security-mode combination.

The libvirt 9.9 check below is an API floor, not a support claim. Passing it
allows an operator to run acceptance tests on another configuration; it does
not add that configuration to this table. Before relying on another host,
run `tests/test_snapshot_e2e.py` on disposable guests, including the lifecycle
checks after successful and recovered operations, and record the exact host
versions, security mode, revision and results. Failure-injection and Backup
interoperability acceptance must also be recorded before claiming the same
coverage as the hosts above. Do not disable host security to turn a failed
acceptance run into a pass.

## Runtime preflight checks

Creation checks all of these before it changes anything, and refuses otherwise:

- libvirt daemon API floor of 9.9 through the QEMU driver (`qemu:///system`).
  This is separate from the tested host configurations above.
- A VM managed by kvm-vm (state manifest and effective definition present).
- The VM is running or shut off (not paused).
- Exactly one system disk: a regular, standalone qcow2 file whose live,
  persistent and managed paths agree. No backing file, internal snapshot,
  persistent bitmap or external data file. CD-ROMs such as the cloud-init seed
  are excluded from the Snapshot; floppy and other disk devices are refused.
- BIOS boot: UEFI firmware, NVRAM and TPM state are not captured, so these VMs
  are refused.
- No Backup checkpoints (see [Snapshots and Backups](#snapshots-and-backups)), no other libvirt snapshot,
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
one, and 1 when something needs the operator:

- **none**: no Snapshot.
- **ready**: the record, the libvirt snapshot and both domain XMLs agree on
  the overlay and the recovery point.
- **needs attention**: an operation (`creating`, `deleting` or
  `rolling-back`) failed or was interrupted, the record cannot be read, or the
  Snapshot was changed outside kvm-vm. The output lists each problem and the
  next step.
- **other libvirt snapshots**: snapshots that kvm-vm did not create. kvm-vm
  does not delete or roll them back, and refuses to create one of its own.

`kvm-vm status NAME` includes the same summary.

## Roll back

```sh
kvm-vm snapshot rollback web01 --yes --force       # running VM
kvm-vm snapshot rollback web01 --yes --no-start    # shut off VM, leave it off
```

Rollback discards everything changed after the Snapshot and returns the VM to
the recovery point:

- The disk returns to `NAME.qcow2` as it was at creation. The overlay with the
  newer writes is deleted.
- The persistent domain configuration captured at creation is defined again,
  so vCPU, memory and device changes made since are undone. The effective
  YAML, the state manifest and the cloud-init files are put back from the
  record.
- The VM keeps its identity: UUID, MAC address, machine ID, cloud-init
  instance ID and SSH host keys. Nothing is reprovisioned or customized, and
  cloud-init sees the same instance.
- The guest boots fresh; RAM and running processes are not brought back.

Rollback uses up the Snapshot: the record and libvirt's snapshot metadata are
removed and the disk is again one standalone file. Create a new Snapshot before
trying the change again. kvm-vm does this itself instead of calling libvirt's
revert, which keeps the Snapshot and puts the VM on another overlay.

Rollback asks for confirmation (`--yes` in scripts) and, like `reinstall` and
`delete`, needs `--force` to stop a running VM. It refuses before any change
when the Snapshot is not ready, when Backup checkpoints exist (they describe
data that the rollback discards), when the captured configuration does not
match the Snapshot, or when the recovery-point disk is not a standalone qcow2.
The output reports how long the rollback took until the VM was started; the
guest still needs its normal boot time before it is usable.

## Failures and interruptions

kvm-vm never continues a failed or interrupted Snapshot operation by itself
([ADR 0005](adr/0005-snapshot-failures-stop-for-the-operator.md)). It keeps
the record, marks the Snapshot as needing attention and says what to do next.
Only a ready Snapshot can be rolled back.

| Situation | Result | What to do |
| --- | --- | --- |
| Agent missing, freeze fails, or libvirt refuses the snapshot | Guest thawed, record removed, disk unchanged | Fix the cause or use `--no-quiesce` |
| Ctrl-C or SIGTERM while the guest is frozen | Ignored until the guest is thawed; the command finishes normally | Nothing |
| Thaw fails after the snapshot was created | Snapshot kept as ready with a warning; command fails | Run `virsh -c qemu:///system domfsthaw NAME` at once |
| Creation stopped for another reason, or kvm-vm was killed | Record stays `creating` | If libvirt lists the Snapshot and the VM uses the overlay, `kvm-vm snapshot delete NAME --yes` merges it; if neither, it removes the record |
| kvm-vm killed while the guest was frozen | Status shows the freeze without a thaw | Thaw with `virsh domfsthaw`, then as above |
| Deletion fails or is interrupted (for example disk full, or the AppArmor case below) | Record stays `deleting`; rollback is disabled, because the recovery-point disk may already hold newer data; the current data is intact | Fix the cause, shut the VM down and run `kvm-vm snapshot delete NAME --yes` again |
| A shut off delete stopped after the VM was redefined | The VM uses its disk again, libvirt still lists the Snapshot | `virsh snapshot-delete NAME SNAPSHOT --metadata`, then `kvm-vm snapshot delete NAME --yes` |
| Rollback fails or is interrupted | Record stays `rolling-back`; the overlay is kept; `snapshot delete`, `snapshot rollback` and the lifecycle commands refuse | Finish the rollback by hand (below) |
| VM fails to start after a rollback | Rollback is complete; the Snapshot is gone | Start the VM with `virsh start` after fixing the cause |
| Record unreadable (for example a crash right after it was created) | Status says so; the lifecycle commands refuse | Once no snapshot or overlay is in use, `kvm-vm snapshot delete NAME --yes` removes the record |
| Snapshot changed with virsh or another tool | Status reports the mismatch; delete and rollback refuse | Inspect `virsh snapshot-list` and the domain disks by hand |

To finish an interrupted rollback, use the files in
`KVM_VM_STATE_DIR/snapshots/NAME/` (status prints the paths):

```sh
virsh -c qemu:///system destroy NAME                     # if it is running
cp -p effective.yaml DEFINITION                          # the path in state.json "definition"
cp -p state.json KVM_VM_STATE_DIR/NAME.json
cp -a cloud/. CLOUD_DIR/                                  # the path in state.json "cloud_dir"
virsh -c qemu:///system define domain.xml
virsh -c qemu:///system snapshot-delete NAME SNAPSHOT --metadata
kvm-vm snapshot delete NAME --yes                        # removes the overlay and the record
virsh -c qemu:///system start NAME
```

When only the record is left, `kvm-vm snapshot delete` removes it, together
with a leftover overlay that no domain and no image in `KVM_VM_DISK_DIR` uses.
A referenced overlay is kept with a warning. The record is removed with a
single rename, so an interruption never leaves part of it behind.

## Working with other commands and tools

`snapshot create`, `snapshot delete` and `snapshot rollback` take the same lifecycle lock as
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

## Snapshots and Backups

A Snapshot does not protect against losing the host or its storage; use the
[Backup procedure](full-backup-restore.md) for that. A Snapshot and incremental
Backup tracking (libvirt checkpoints and their bitmaps) are never combined:
kvm-vm resets the Backup baseline around each Snapshot instead of carrying it
across. This sequence was tested end to end, including a Restore of the next
Backup history on another host (see the
[ticket 07 record](plans/20261018_vm-recovery/ticket-07-validation.md)):

1. Optionally take a final incremental Backup of the current history.
2. Retire the tracking while the VM runs:
   `virsh -c qemu:///system checkpoint-delete NAME virtnbdbackup.0 --children`.
   This removes the checkpoints and their bitmaps. The existing Backup set is
   unchanged and stays restorable; an incremental Backup into it is refused
   from now on.
3. `kvm-vm snapshot create NAME`, then make the change.
4. Keep the change with `kvm-vm snapshot delete NAME --yes`, or discard it with
   `kvm-vm snapshot rollback NAME --yes --force`.
5. Start the next Backup history with a full Backup into a new directory and
   capture the management files of each point, as in
   [capture each recovery point](full-backup-restore.md#capture-each-recovery-point).
   Later incremental Backups continue that new history.

kvm-vm enforces the reset. `snapshot create`, `snapshot delete` and
`snapshot rollback` refuse a VM with checkpoints, change nothing, and print
the retire command from step 2. A Backup started while a Snapshot is being
created is refused the same way, because the Backup tool creates its checkpoint
when the job starts. Do not start Backups while a Snapshot exists; if one ran
anyway, retire its checkpoint before deleting or rolling back the Snapshot.

A Backup tool that is killed in the middle leaves a running Backup job, its new
checkpoint and temporary bitmaps. kvm-vm refuses the VM until you end the job
with `virsh domjobabort NAME` and retire the checkpoint as in step 2. Throw
away the incomplete Backup directory.

kvm-vm sees Backup tracking through libvirt's checkpoints. For a running VM,
QEMU writes persistent bitmaps into the image only when it closes it, so
kvm-vm cannot see a bitmap whose checkpoint metadata was removed by hand
(`checkpoint-delete --metadata`). Do not remove only the metadata; the
[Backup procedure](full-backup-restore.md#start-the-next-backup-history)
explains why.

Filesystem freezes by kvm-vm and by the Backup tool give filesystem
consistency only. Application consistency needs the workload's own procedure.
