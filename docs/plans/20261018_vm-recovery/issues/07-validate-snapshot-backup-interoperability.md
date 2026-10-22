# 07: Verify Snapshot rollback and incremental Backup interoperability

**What to build:** Operators have a tested, documented sequence combining
Snapshots with external full/incremental Backups, rollback and subsequent
recovery. The sequence either preserves a valid incremental baseline or
explicitly creates a new full baseline, and its resulting Backups can Restore
the expected managed VM.

**Blocked by:** 06: Roll back Snapshot disks and corresponding VM configuration.

**Status:** complete; see the [validation record](../ticket-07-validation.md).

**Spec coverage:** User Stories 16, 18, 19, 20, 21, 22, 23, 24, 34, 36, 37, 38,
39 and 40; ADR-0001.

- [x] Demonstrate at least one supported end-to-end sequence containing
  Snapshot creation, full/incremental Backups, rollback, subsequent Backup and
  Restore. Rejecting every combination is not a completed interoperability
  workflow; explicitly sequenced baseline resets are acceptable.
- [x] Exercise checkpoint/bitmap state before and after rollback. Document and
  test whether the existing baseline remains usable or requires a new full
  Backup. Never silently continue a known invalid incremental history.
- [x] Verify the subsequent full/incremental recovery point by restoring onto
  a compatible destination, booting and checking expected data, configuration,
  identity and kvm-vm managed behavior. Use the independent-artifact recovery
  workflow from tickets 03 and 04.
- [x] Validate supported creation and cleanup sequences when Backup checkpoints
  already exist. Unsupported operation/version combinations refuse safely and
  explain the supported alternative without discarding usable recovery data.
- [x] Exercise invalid or missing tracking state, external backup failure,
  interruption and conflicting disk jobs. Failed operations preserve needed
  dependencies and expose recovery steps; any guest freeze has a thaw path.
- [x] Recheck clone, reinstall, delete and keep-storage after successful and
  failed recovery sequences. Their documented support/refusal behavior remains
  consistent with the data-preservation guarantees established earlier.
- [x] Keep filesystem consistency distinct from workload-specific application
  consistency, and document operational exclusions for directly invoked tools.
  Do not assume external commands honor kvm-vm's concurrency coordination.
- [x] Extend command-boundary regression coverage for integration changes and
  execute the real Linux/KVM workflow. A skipped scenario or process exit alone
  does not satisfy guest-visible recovery acceptance.
- [x] Publish the tested version/configuration matrix, supported sequencing,
  artifact requirements, measured pauses/recovery duration and actual results
  in project documentation. Keep unvalidated combinations explicitly limited.
- [x] Commit the completed interoperability changes, tests and evidence with a
  Conventional Commit and detailed body after all relevant checks pass.
