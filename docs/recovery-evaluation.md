# Snapshot and backup/restore evaluation

Status: scope and implementation order accepted in
[ADR 0001](adr/0001-snapshot-and-backup-ownership.md). This document records the
requirements, decision and findings as agreed on 2026-10-17, before any
implementation; the findings and open questions below describe that point in
time. All seven tickets of the implementation plan are now complete, including
cross-host Restore and Snapshot validation on real KVM hosts. Each
[ticket validation record](plans/20261018_vm-recovery/) states where it was
tested. For current behavior see [Snapshots](snapshots.md) and the
[Backup and Restore procedure](full-backup-restore.md); later decisions are in
[ADR 0004](adr/0004-pre-restore-backup-check.md) and
[ADR 0005](adr/0005-snapshot-failures-stop-for-the-operator.md). The
[External snapshot tools](#external-snapshot-tools) section was added on
2026-10-20.

The [implementation specification](plans/20261018_vm-recovery/spec.md) captures the user
stories, behavioral contracts and acceptance tests for this accepted scope.

## Confirmed requirements

- Evaluate rollback after unwanted changes separately from disaster recovery.
- Creating a snapshot must not require shutting down the VM; rollback may do so.
- Brief filesystem freeze or I/O pauses during creation are acceptable. There
  is no requirement for completely uninterrupted service during creation.
- A snapshot covers disks and the corresponding VM configuration. Restarting
  the guest after rollback is acceptable; restoring RAM or running processes is
  not required.
- The first version keeps at most one short-lived snapshot per VM. It supports
  creation, inspection, rollback and deletion. Remove the snapshot after
  confirming an update succeeded; long-term history belongs to backups.
- Fast local rollback has independent value; waiting for full disk reconstruction
  does not satisfy that need. No numerical recovery-time or pause limit has been
  specified, so this evaluation makes no timing guarantee.
- Disaster recovery must work after loss of the original host or storage: on a
  compatible replacement host, the VM must boot and remain managed by kvm-vm.
- Operators may invoke virtnbdbackup and virtnbdrestore directly. A unified
  kvm-vm command interface is not itself a requirement.
- The near-term backup scope is operational documentation, an inventory of
  management artifacts and cross-host recovery validation. Add commands in
  response to demonstrated correctness or operational gaps.

See [the recovery glossary](../GLOSSARY.md) for the agreed terminology.

## Accepted decision

Delegate disk backup and reconstruction to virtnbdbackup and virtnbdrestore.
Their existing online full, incremental and differential backup support makes
building another backup engine difficult to justify for this project.

Add bounded native snapshot management using libvirt because fast local rollback
is required independently of disaster recovery. Its scope includes creation,
inspection, rollback, cleanup and compatibility with existing VM operations.
The first version's single short-lived snapshot avoids the need to manage a
branching snapshot history, but does not eliminate disk-chain responsibilities.

For disaster recovery, invest in recovery of kvm-vm's own management information
and in lifecycle compatibility. Document and verify a complete recovery
procedure before adding commands solely to wrap the external tools.

## Findings that affect the decision

1. Each managed VM currently has an independent qcow2 system disk. External
   snapshots can change the active disk path, while clone, reinstall and delete
   rely on the disk path in the state manifest. Supporting external snapshots
   requires accounting for the disk chain throughout these operations.
2. Restoring disks and libvirt XML alone does not restore managed status.
   Effective YAML, the state manifest and cloud-init artifacts also need a
   coherent recovery procedure. Paths, host networks and VM identity must be
   checked on the destination. Clone is not a restore procedure: it deliberately
   resets guest identity.
3. Incremental backup checkpoints expose an existing lifecycle risk.
   `undefine_domain` supplies `--snapshots-metadata` but not
   `--checkpoints-metadata`. Libvirt refuses to undefine an inactive domain with
   checkpoint metadata unless explicitly allowed. On this failure, reinstall
   calls `cleanup_failed`, which ignores further undefine failures and can
   unlink the original disk. This control flow was reproduced using temporary
   marker files and mocked virsh failures: the actual reinstall and cleanup
   functions deleted the original marker while every undefine attempt failed.
   YAML and preflight checks were stubbed; subprocess and network calls were
   blocked. The checkpoint refusal condition is documented by libvirt but has
   not been reproduced on a hypervisor in this investigation.
   Resolve and test this failure path before claiming supported incremental
   backup interoperability. Simply adding a metadata deletion flag is not a
   complete failure-safety design.
4. Snapshot creation without shutdown does not imply uninterrupted service.
   Current libvirt's QEMU driver rejects online internal disk-only snapshots;
   online internal snapshots include memory and pause guest CPUs while saving.
   External disk-only snapshots introduce the disk-chain responsibilities above.
5. Snapshot rollback and incremental backup tracking require version-specific
   validation. Do not assume the existing incremental chain remains valid after
   reverting VM storage. Filesystem freeze alone also does not establish
   application-level consistency for every guest workload.

## Follow-up implementation design and validation

The value and high-level scope questions are settled. Before implementation,
establish the supported libvirt/QEMU versions and guest/storage configurations,
define failure and concurrency behavior, and determine how rollback coordinates
libvirt configuration, kvm-vm management artifacts and backup checkpoints.
Measure creation pauses and recovery time on representative workloads. These
are follow-up design and verification tasks, not capabilities established by
this discussion.

## Agreed work order and validation

This discussion changes documents only; review their contents and run
`git diff --check`. VM integration tests are not needed for documentation edits.
Implement recovery support in this order, keeping tasks in separate commits
and finishing each task's checks before moving to the next:

1. Fix lifecycle failure safety and checkpoint handling, with unit tests proving
   that a failed undefine cannot delete the original disk or guest artifacts.
2. Document the recovery artifacts and procedure; add metadata helpers only when
   a concrete correctness or operational gap justifies them.
3. Validate recovery on Linux/KVM using a separate compatible destination:
   write identifiable guest data, back it up with management artifacts, recover
   without relying on the original host, then verify boot, data and managed
   operations. Include failure and checkpoint compatibility cases.
4. Implement the bounded snapshot lifecycle and its integration with existing
   commands. Unit tests should cover state transitions and failure handling;
   Linux/KVM integration tests should exercise online creation, guest writes,
   rollback followed by boot, and cleanup that preserves the current guest
   data. Verify configuration consistency, existing VM operations and backup
   checkpoint behavior across rollback. Keep data-loss regression coverage
   alongside the implementation rather than testing only CLI return codes.

## Evidence

- Local implementation: `kvm-vm` functions `save_state`, `cmd_clone`,
  `cmd_reinstall`, `cleanup_failed`, `undefine_domain` and `cmd_delete`.
- [virtnbdbackup modes and restore documentation](https://github.com/abbbi/virtnbdbackup#backup-modes-and-concept).
- [libvirt undefine and checkpoint metadata](https://libvirt.org/manpages/virsh.html#undefine).
- [libvirt domain state capture](https://libvirt.org/kbase/domainstatecapture.html).
- [Current libvirt QEMU snapshot implementation](https://github.com/libvirt/libvirt/blob/master/src/qemu/qemu_snapshot.c).
- [QEMU guest filesystem freeze](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html#command-guest-fsfreeze-freeze).

Upstream references were inspected on 2026-10-17. At that time, actual host
versions and workloads had not been established and no cross-host recovery had
been run; the ticket validation records cover both since.

## External snapshot tools

Checked on 2026-10-20: is there a Snapshot tool that kvm-vm could call, the way
it leaves disk Backups to virtnbdbackup and virtnbdrestore, so that it does not
manage the details itself?

No maintained command-line tool was found at that level. The closest tool is
libvirt itself, and kvm-vm already uses it where it works.

| Option | Usable by kvm-vm | Reason |
| --- | --- | --- |
| libvirt / virsh: external snapshot deletion since 9.0, revert since 9.9 | Already used | Creation and live deletion use libvirt. Offline deletion is not used because of the AppArmor rule on Debian 13 (libvirt issue #806). Revert keeps the snapshot, puts the VM on another new overlay, always restarts the VM process (since libvirt 7.10) and does not restore kvm-vm's own files. |
| Internal snapshots, stored inside the qcow2 file | No | libvirt handles creation, revert and deletion, and the disk stays one file. But libvirt's QEMU driver rejects disk-only internal snapshots of a running VM, so they include memory and pause the guest CPUs while saving (finding 4). RHEL 10 documentation calls internal snapshots deprecated. |
| virt-manager, Cockpit Machines | No | Graphical tools that a command cannot call. Cockpit Machines 306 uses external snapshots with libvirt 9.9 or newer, which is libvirt's own support. |
| Proxmox VE (`qm snapshot`), oVirt | No | Complete platforms. They would replace kvm-vm rather than be called by it. |
| Scripts found on GitHub | No | Mostly backup tools or tutorials, for example virt-backup, a Ruby backup and restore tool. No maintained Snapshot manager. |

### Why Snapshots have no virtnbdbackup-like tool

- A Backup reads the disk and writes files elsewhere, and a Restore produces
  new files. Neither changes the running VM, so a separate tool can own them.
  A Snapshot changes the disk chain that the running VM uses, so the point of
  integration is the domain itself.
- What kvm-vm manages is unknown to any outside tool: capturing and restoring
  the effective definition, state manifest and cloud-init files; one Snapshot
  per VM; refusing Backup checkpoints; the lifecycle lock; and the refusals in
  clone, reinstall and delete. These stay in kvm-vm whichever tool does the
  disk work.

### Using `virsh snapshot-revert` for rollback

It saves little. After a revert, the Snapshot remains and the VM runs on a new
overlay. To keep "rollback uses up the Snapshot", kvm-vm would still delete it
afterwards. On Debian 13 that delete hits libvirt issue #806, because QEMU was
started with the Snapshot in place, so the offline `qemu-img commit` is still
needed. kvm-vm's own files still have to be restored too. Ticket 06 chose to
implement rollback without libvirt's revert (see
[Roll back](snapshots.md#roll-back)), and that choice still holds.

### Storage snapshots

The only way to hand the disk details to another tool is a storage-level
snapshot, for example one ZFS dataset per VM. It has no overlay, no merge and
no AppArmor issue. The costs:

- The project would require a host storage layout. ZFS is not part of a
  default AlmaLinux or Debian install.
- Raw LVM volumes would lose incremental Backups, because libvirt supports
  checkpoints only for qcow2 disks at the active layer.
- A storage rollback also rolls back the bitmaps inside the qcow2 file, while
  libvirt's checkpoint metadata stays as it was, so the checkpoint rules
  remain. This follows from where each is stored; it was not tested.

Not adopted, unless the project decides to require a host storage layout.

### Conclusion

Keep libvirt as the base, and kvm-vm as a thin layer for its rules and its own
management files. Simplification belongs in kvm-vm's recovery logic
([ADR 0005](adr/0005-snapshot-failures-stop-for-the-operator.md)), not in a
change of tools.

### Sources

- [libvirt release notes](https://libvirt.org/news.html): 9.0.0 adds external
  snapshot deletion, 9.9.0 adds external snapshot revert.
- [virsh manual: snapshot-revert](https://libvirt.org/manpages/virsh.html#snapshot-revert):
  the VM process is always restarted since 7.10.0.
- [libvirt patch: reverting to an external snapshot creates new overlay files](https://lists.libvirt.org/archives/list/devel@lists.libvirt.org/thread/BDTDAD3JARDQ65TTXYABHQBMYUSN2AMK/).
- [libvirt checkpoint XML format](https://libvirt.org/formatcheckpoint.html):
  incremental Backups need qcow2 disks at the active layer.
- [libvirt issue #806](https://gitlab.com/libvirt/libvirt/-/issues/806).
- [RHEL 10: saving and restoring VM state by using snapshots](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/configuring_and_managing_windows_virtual_machines/saving-and-restoring-virtual-machine-state-by-using-snapshots):
  internal snapshots are deprecated.
- [Cockpit 310 release notes](https://cockpit-project.org/blog/cockpit-310.html):
  Cockpit Machines 306 uses external snapshots with libvirt 9.9.0 or later.
- [virt-backup](https://github.com/eslam-gomaa/virt-backup): the closest
  script found, a backup and restore tool.
- [Snapshots](snapshots.md) and the
  [ticket 06 validation](plans/20261018_vm-recovery/ticket-06-validation.md).
