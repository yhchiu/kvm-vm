# Snapshot and backup/restore evaluation

Status: in discussion. This document records requirements and findings, not a
claim that recovery workflows have been implemented or tested on a hypervisor.

## Confirmed requirements

- Evaluate rollback after unwanted changes separately from disaster recovery.
- Creating a snapshot must not require shutting down the VM; rollback may do so.
- Fast local rollback has independent value; waiting for full disk reconstruction
  does not satisfy that need. A numerical recovery-time target remains open.
- Disaster recovery must work after loss of the original host or storage: on a
  compatible replacement host, the VM must boot and remain managed by kvm-vm.
- Operators may invoke virtnbdbackup and virtnbdrestore directly. A unified
  kvm-vm command interface is not itself a requirement.
- The near-term backup scope is operational documentation, an inventory of
  management artifacts and cross-host recovery validation. Add commands in
  response to demonstrated correctness or operational gaps.

See [the recovery glossary](../GLOSSARY.md) for the agreed terminology.

## Current recommendation

Delegate disk backup and reconstruction to virtnbdbackup and virtnbdrestore.
Their existing online full, incremental and differential backup support makes
building another backup engine difficult to justify for this project.

Native snapshot management is worth evaluating because fast local rollback is
required independently of disaster recovery. Its scope must include rollback,
cleanup and compatibility with existing VM operations, not just creation.

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

## Questions still open

- What recovery-time target and snapshot state coverage justify the maintenance
  cost of native online snapshot management?
- Are brief filesystem/I/O pauses acceptable while creating the snapshot?
- Is one short-lived snapshot per VM sufficient, or is history management needed?

## Validation and possible work split

This discussion changes documents only; review their contents and run
`git diff --check`. VM integration tests are not needed for documentation edits.
If recovery support is taken forward, keep these tasks in separate commits and
finish each task's checks before moving to the next:

1. Fix lifecycle failure safety and checkpoint handling, with unit tests proving
   that a failed undefine cannot delete the original disk or guest artifacts.
2. Document the recovery artifacts and procedure; add metadata helpers only when
   a concrete correctness or operational gap justifies them.
3. Validate recovery on Linux/KVM using a separate compatible destination:
   write identifiable guest data, back it up with management artifacts, recover
   without relying on the original host, then verify boot, data and managed
   operations. Include failure and checkpoint compatibility cases.

## Evidence

- Local implementation: `kvm-vm` functions `save_state`, `cmd_clone`,
  `cmd_reinstall`, `cleanup_failed`, `undefine_domain` and `cmd_delete`.
- [virtnbdbackup modes and restore documentation](https://github.com/abbbi/virtnbdbackup#backup-modes-and-concept).
- [libvirt undefine and checkpoint metadata](https://libvirt.org/manpages/virsh.html#undefine).
- [libvirt domain state capture](https://libvirt.org/kbase/domainstatecapture.html).
- [Current libvirt QEMU snapshot implementation](https://github.com/libvirt/libvirt/blob/master/src/qemu/qemu_snapshot.c).
- [QEMU guest filesystem freeze](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html#command-guest-fsfreeze-freeze).

Upstream references were inspected on 2026-10-17. Actual host versions and
workloads have not been established; no cross-host recovery has been run.
