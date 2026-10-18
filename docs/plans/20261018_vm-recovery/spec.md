---
status: ready-for-agent
decision: ADR-0001
---

# Bounded snapshots and recoverable external backups

## Problem Statement

Operators of long-running standalone KVM/libvirt hosts need two different forms
of recovery. Before an update, they need a Snapshot that can undo an unwanted
change quickly without shutting down the guest to create it. After losing a
host or its storage, they need a Backup from which they can Restore a usable VM
on a compatible replacement host.

Waiting for complete disk reconstruction does not satisfy the fast local
rollback requirement. Conversely, a Snapshot alone does not protect against
loss of the original storage. Restoring a disk and libvirt configuration also
does not restore kvm-vm's effective definition, management state or cloud-init
artifacts automatically.

Existing lifecycle operations assume the disk recorded in the state manifest
is the disk to operate on. External snapshots can invalidate that assumption.
Incremental backup checkpoints also expose a reproduced failure: if domain
undefinition fails during reinstall, error cleanup can delete the original
disk even though the old domain still exists. Recovery support must address
these interactions before it can be considered usable.

## Solution

Provide one short-lived Snapshot per VM for an update or configuration change.
Operators can create it while the VM runs, inspect it, roll back to it, and
delete it after confirming an update succeeded. Brief filesystem freeze or I/O
pauses during creation are acceptable. Rollback restores disks and the
corresponding VM configuration, and may stop and restart the guest. Restoring
RAM or process execution state is not required.

Use virtnbdbackup and virtnbdrestore directly for disk Backup and Restore.
kvm-vm supplies an operational recovery procedure that includes its management
information and is validated on a compatible replacement host without relying
on the original host or storage. Add helper commands only where a demonstrated
correctness or operational gap warrants them.

Deliver the work in the agreed order: correct lifecycle failure safety,
document and validate external backup recovery, then add the bounded Snapshot
lifecycle. The existing lightweight CLI architecture remains the integration
boundary.

## User Stories

1. As a VM operator, I want Snapshot rollback and Backup Restore to have distinct meanings, so that I choose recovery appropriate to the failure.
2. As a VM operator, I want to create a Snapshot before an update while the guest remains running, so that preparing for rollback does not require shutdown.
3. As a VM operator, I want creation pauses and consistency expectations documented, so that I can judge their effect on the guest workload.
4. As a VM operator, I want at most one short-lived Snapshot per VM, so that I can maintain a simple update-and-recover workflow.
5. As a VM operator, I want an attempt to create a second Snapshot to preserve the existing one and explain the conflict, so that my recovery point is not silently replaced.
6. As a VM operator, I want to inspect whether a Snapshot exists and whether an operation needs recovery, so that I know what actions are available.
7. As a VM operator, I want Snapshot inspection to identify the VM and recovery point, so that I can verify the target before rollback or deletion.
8. As a VM operator, I want to roll back disk contents to the captured point and boot the VM, so that I can recover from a failed update quickly.
9. As a VM operator, I want the corresponding VM configuration restored with the disks, so that the recovered guest uses settings appropriate to that recovery point.
10. As a VM operator, I want libvirt configuration and kvm-vm management information to agree after rollback, so that later commands operate on the recovered VM correctly.
11. As a VM operator, I want rollback to preserve the VM's identity rather than apply clone customization, so that recovery does not create an unintended new machine.
12. As a VM operator, I want to confirm destructive rollback through the tool's established interaction conventions, so that I do not discard current changes accidentally.
13. As a VM operator, I want to delete a Snapshot after a successful update while preserving the current guest data, so that cleanup does not undo the update.
14. As a VM operator, I want cleanup to remove only artifacts no longer needed by the VM, so that a backing disk still in use is never deleted.
15. As a VM operator, I want to create a new Snapshot after the previous one is removed, so that the same workflow supports the next update.
16. As a VM operator, I want unsupported host capabilities or storage configurations rejected before destructive changes, so that partial recovery coverage is not presented as complete protection.
17. As a VM operator, I want disk topology or configuration changes outside kvm-vm detected before unsafe operations, so that stale management state does not direct commands to the wrong disk.
18. As a VM operator, I want failed Snapshot creation to release any guest freeze and report the actual resulting state, so that the guest is not left stuck behind a false success message.
19. As a VM operator, I want interrupted Snapshot operations to preserve recoverable data and expose actionable recovery information, so that retrying does not compound the failure.
20. As a VM operator, I want conflicting disk or recovery operations prevented or clearly refused, so that one operation cannot invalidate another's data.
21. As a VM operator, I want clone to use current guest data or explicitly refuse an unsupported Snapshot state, so that a clone never silently contains an old base disk's contents.
22. As a VM operator, I want reinstall to handle Snapshot and Backup checkpoint state deliberately or refuse safely, so that it does not silently orphan recovery artifacts or delete a disk on failure.
23. As a VM operator, I want delete to handle Snapshot dependencies safely or explain why it cannot proceed, so that cleanup neither leaks untracked disk chains nor removes data still in use.
24. As a VM operator, I want keep-storage behavior to retain the storage dependencies needed for recovery, so that preserving one file does not leave an unusable VM image.
25. As a VM operator, I want a failed domain undefinition to preserve the original disk, management records and cloud-init artifacts, so that an unsuccessful reinstall does not destroy the machine it failed to replace.
26. As a VM operator, I want operations that do not use external backup tools to remain usable without installing those tools, so that backup integration stays optional.
27. As a backup operator, I want to use virtnbdbackup and virtnbdrestore directly, so that I can use their existing disk backup and reconstruction capabilities.
28. As a backup operator, I want an explicit inventory of VM and kvm-vm recovery artifacts, so that a Backup includes more than just disk data.
29. As a backup operator, I want management information associated with the relevant recovery point, so that I do not combine historical disks with incompatible current configuration.
30. As a backup operator, I want the Backup retained independently of the original host and storage, so that losing them does not also lose the recovery source.
31. As a recovery operator, I want a documented procedure for destination paths, host networks and identity conflicts, so that I can Restore onto a compatible replacement host safely.
32. As a recovery operator, I want Restore to preserve the intended guest and cloud-init identity, so that recovering an existing machine does not accidentally rerun provisioning as a new one.
33. As a recovery operator, I want the restored VM to appear as managed and support the applicable management commands, so that recovery includes continued operation through kvm-vm.
34. As a recovery operator, I want to verify both Backup integrity and the restored guest's boot and data, so that a checksum or successful process exit is not mistaken for a complete recovery test.
35. As a recovery operator, I want a recovery exercise that cannot read the original host's artifacts, so that success proves the Backup is sufficient.
36. As a backup operator, I want rollback and Restore to have documented consequences for Backup checkpoints, so that later incremental backups never silently follow an invalid history.
37. As a backup operator, I want an invalid incremental baseline to be refused or explicitly replaced through the documented full-backup procedure, so that unusable backup chains are not reported as valid.
38. As a backup operator, I want filesystem consistency and workload-specific application consistency distinguished, so that guest-agent availability is not treated as a universal database recovery guarantee.
39. As a VM operator, I want measured creation pauses and rollback duration on supported configurations, so that I can assess whether the feature fits my workload.
40. As a project maintainer, I want lifecycle failures and real recovery outcomes tested through existing command interfaces, so that refactoring internal helpers does not invalidate the tests' purpose.

## Implementation Decisions

The following decisions and acceptance constraints implement the accepted
ownership and scope in ADR 0001. Internal schemas and command spelling have not
been selected by the discussion.

1. **Ownership and architecture.** kvm-vm owns its Snapshot lifecycle and
   management information. Libvirt performs hypervisor snapshot operations;
   virtnbdbackup and virtnbdrestore own disk backup and reconstruction. Preserve
   the existing approach of invoking system CLIs rather than introducing a new
   in-process hypervisor binding requirement.
2. **Affected responsibilities.** Extend the existing command interface, VM
   state persistence and lifecycle orchestration. Integrate with clone,
   reinstall, delete and status/inspection where their behavior depends on
   Snapshot state. Do not build a separate backup engine or orchestration
   service for this scope.
3. **Failure safety comes first.** Correct the reinstall path that conflates
   failure before replacement with cleanup of a newly created VM. When
   undefinition is refused, preserve the original disk and associated artifacts.
   Temporary resources created by the failed attempt may be cleaned up only
   when their ownership is established. Adding a checkpoint-removal flag alone
   does not satisfy this requirement.
4. **Snapshot contract.** Support creation, inspection, rollback and deletion,
   with at most one short-lived Snapshot per VM. A second creation must not
   silently replace the existing recovery point. Deletion retains current guest
   contents; rollback intentionally returns to captured contents. Creation
   must not shut down the guest; brief freeze/I/O pauses are permitted. Rollback
   may stop and restart it. No RAM capture or resume semantics are required.
5. **Configuration coverage.** Keep the captured disk state, corresponding
   libvirt configuration and kvm-vm management information coherent. Account for
   the effective definition, state manifest and cloud-init artifacts/identity.
   Recovery is not clone or reinstall, and must not silently regenerate guest
   identity through those provisioning workflows.
6. **Disk topology is part of the lifecycle.** Never assume the stored original
   disk path remains the active image after a Snapshot. Validate the actual
   topology before copying, replacing, merging or removing storage. Existing
   operations must either handle a Snapshot safely or refuse with an actionable
   explanation. Keep-storage must preserve the dependencies of retained data.
   The presence of externally created or untracked artifacts is not permission
   to delete them.
7. **Persistent state and interruption.** Persist enough information to identify
   the recovery point, relate it to the VM and management artifacts, and expose
   incomplete work. An interrupted operation must not be reported as complete.
   Define and test recovery/retry behavior before enabling a mutating command.
   Existing managed VMs without Snapshot information must remain usable; this
   spec does not mandate a particular new schema or internal state machine.
8. **Conflicts and consistency.** Define how concurrent lifecycle operations and
   active hypervisor disk jobs are detected or excluded. Any guest freeze must
   have a thaw path when the surrounding operation fails. Directly invoked
   external tools cannot be assumed to honor kvm-vm's coordination mechanism;
   the procedure must state supported sequencing and operational exclusions.
   Unsupported consistency or capability conditions must be explicit, not a
   silent downgrade of the advertised recovery coverage.
9. **Backup checkpoints.** A Backup checkpoint is change-tracking information,
   not a Snapshot or independently usable Backup. Integrate checkpoint handling
   with undefinition, reinstall, delete, rollback and Restore. Do not assume
   tracking remains valid after storage rollback. Document and test whether a
   supported operation preserves the baseline or requires a new full Backup;
   never silently continue a known invalid incremental history.
10. **External recovery procedure.** Document the relevant tool prerequisites,
    disk/domain recovery artifacts, effective definition, state manifest and
    cloud-init information. Preserve the association between the Backup and its
    management artifacts, including when definitions change between recovery
    points. Describe independently retained storage, integrity verification,
    destination preparation, path/network mapping and name/identity conflicts.
    Explicitly inventory firmware or other non-disk state required by the
    supported configuration instead of assuming all VM state is included.
11. **Successful Restore.** A compatible replacement host can boot the recovered
    VM, expose its expected data and identity, and manage it through kvm-vm using
    only independently retained recovery material and documented host
    prerequisites. Direct external backup/restore commands are acceptable. Add
    metadata export/import or backup wrapper commands only when validation
    demonstrates a concrete gap in the documented procedure.
12. **Compatibility and implementation latitude.** Establish and document a
    tested support matrix before claiming support. The current managed baseline
    is a standalone host with an independent qcow2 system disk; this feature
    does not promise support for every administrator-added device. Select exact
    libvirt operations, version/capability gates, metadata representation,
    command syntax, concurrency strategy and whether rollback consumes or
    retains the single Snapshot during implementation. These choices must obey
    the contracts above and be visible in documentation/tests. Unsupported
    configurations must be refused before unsafe mutation rather than silently
    receiving incomplete protection.

## Testing Decisions

Use the existing operator-facing command boundary as the primary test seam.
Real Linux/KVM tests invoke the CLI and inspect guest-visible behavior. Fast
failure tests enter through existing command handlers, keep orchestration and
cleanup real, and replace external effects at the subprocess/environment
boundary. This extends the accepted unit and real-machine test strategy without
introducing a separate test-only module interface.

A good test demonstrates an externally meaningful result: preserved original
bytes after refusal, the correct guest data after rollback, a usable managed VM
after Restore, or a clear refusal before mutation. Prefer these assertions to
helper-call counts, private layout details or matching the implementation's
internal sequence.

- **Prior art.** Reuse the repository's pytest command-handler tests with
  temporary files, including the successful reinstall flow and the existing
  disk-creation-failure protection case. The real-machine lifecycle suite already
  invokes create, status/list, clone, reinstall and delete, waits for guest SSH,
  and inspects in-guest state. Extend those fixtures and helpers instead of
  creating parallel harnesses for each recovery operation.
- **Lifecycle failure regression.** Exercise a command where all domain
  undefinition attempts fail, including the checkpoint refusal condition. Keep
  the actual failure cleanup active and assert that original disk contents,
  definitions, management state and cloud-init artifacts survive. Check that
  only owned temporary resources are removed. Cover refusal before replacement
  and failures after replacement begins without conflating their ownership.
- **Snapshot behavior.** Verify the one-Snapshot limit, absent-state inspection,
  creation/refusal outcomes, configuration capture, rollback, and deletion that
  preserves post-Snapshot writes. Test data and observable state after tool
  failures, interruption, unavailable capabilities, unexpected topology and
  conflicting work. Where creation freezes the guest, inject failure after
  freeze and verify thaw handling and an actionable error outcome.
- **Existing command interoperability.** Exercise clone, reinstall, delete and
  keep-storage with a Snapshot or Backup checkpoint present. Assert current-data
  behavior where supported, or explicit refusal without unintended disk loss.
  Include old managed state without Snapshot metadata and ordinary operations
  with the optional backup tools absent.
- **Online Snapshot integration.** On a supported Linux/KVM host, write
  identifiable guest data, capture a Snapshot while the guest runs, and verify
  the guest did not reboot during creation. Change guest data and relevant VM
  configuration, roll back, boot, and check the captured data, configuration and
  managed identity. Separately delete a Snapshot after successful changes and
  verify those changes survive cleanup. Inspect the resulting disk dependencies
  and exercise subsequent lifecycle operations.
- **Cross-host recovery acceptance.** Use a source and a distinct compatible
  destination. Back up identifiable guest data and the required management
  artifacts, then make the source's data unavailable to the recovery procedure.
  Restore only from retained material, boot, verify data and intended identity
  through SSH, and check managed status and applicable management operations.
  A same-host restore, a successful process exit or checksum verification alone
  does not fulfill this acceptance case.
- **Incremental interoperability.** Exercise a full-plus-incremental backup
  history, Snapshot creation/rollback where supported, and the next Backup and
  Restore. Prove either that the documented baseline remains usable or that the
  workflow explicitly establishes a new full baseline. Include invalid or
  missing checkpoint/bitmap state; never infer validity solely from exit status.
- **Operational evidence.** Record the tested host/tool versions, guest and
  storage configuration, consistency coverage, artifacts used, measured pauses
  and rollback/recovery duration. Exercise the documented recovery steps rather
  than an undocumented shortcut available only to the test author.
- **Execution requirements.** Real acceptance needs Linux, root access, KVM,
  libvirt, guest connectivity and the applicable external backup tools. Missing
  prerequisites may cause an explicit skip, but a skip is not proof of recovery.
  Windows-only or mocked results cannot close the real-machine acceptance work.

## Out of Scope

- A new disk backup engine, backup format or incremental reconstruction engine.
- A mandatory unified backup CLI or metadata helper without a demonstrated gap.
- Multiple retained Snapshots per VM, branching history or long-term Snapshot
  retention policies.
- RAM/process-state capture and restoration.
- Guaranteed zero-pause creation or a numerical pause/recovery-time SLA; the
  discussion established the need for fast rollback without setting a limit.
- An integrated scheduler, backup retention service, storage replication service
  or monitoring dashboard. Operators may compose external tools separately.
- Turning a Snapshot into a disaster-recovery copy by itself.
- Automatic provisioning of replacement hosts, networks or equivalent hardware.
- General drift reconciliation, live migration, high availability, arbitrary
  storage backends or automatic inclusion of administrator-added data disks.
- A blanket application-consistency guarantee for all databases and workloads,
  or universal recovery of unvalidated firmware/TPM/device configurations.

## Further Notes

This local specification synthesizes the confirmed conversation and accepted
[ADR 0001](../../adr/0001-snapshot-and-backup-ownership.md). Use the terminology in
the [recovery glossary](../../../GLOSSARY.md). The [evaluation](../../recovery-evaluation.md)
records the code findings and upstream references. The `ready-for-agent` status
is local triage metadata; it does not assert that features are implemented or
that recovery has already been validated.

Keep the agreed delivery sequence and commit each completed task after its
relevant checks pass:

1. Correct lifecycle failure safety and checkpoint-related behavior, with
   focused regression tests for original-data preservation.
2. Document external backup/Restore and the artifact inventory. Add narrowly
   scoped helpers only if a concrete gap is demonstrated, with behavior tests
   for any helper added.
3. Add and run the cross-host recovery acceptance workflow; record its results
   and compatibility limits. If the required hosts are unavailable, keep this
   work explicitly pending rather than substituting mocked success or silently
   moving past the agreed recovery-validation gate.
4. Implement the bounded Snapshot lifecycle and integration with existing VM
   operations, accompanied by failure tests and Linux/KVM acceptance evidence.

Use Conventional Commits with detailed bodies and no co-author trailer. Exact
internal implementation choices may be made while delivering these tasks;
changes to the accepted product scope require revisiting the ADR.

The prior investigation reproduced the reinstall cleanup data-loss path using
temporary marker files and mocked virsh refusal, while executing the real
reinstall and cleanup functions. YAML and prerequisites were stubbed, and
subprocess/network calls were blocked. It did not run a hypervisor reproduction
or a cross-host recovery exercise. Retain that distinction when reporting
validation, and convert the failure case into a durable regression test during
the first implementation task.
