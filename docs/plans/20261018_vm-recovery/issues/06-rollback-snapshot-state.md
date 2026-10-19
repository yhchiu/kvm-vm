# 06: Roll back Snapshot disks and corresponding VM configuration

**What to build:** After an unwanted update, an operator can confirm rollback,
restore the captured disk and configuration state, and boot the same VM with
coherent kvm-vm management information. Failed or interrupted rollback leaves
recoverable data and an honest, actionable operation state.

**Blocked by:** 05: Create, inspect and delete one short-lived Snapshot.

**Status:** complete; see the [validation record](../ticket-06-validation.md).

**Spec coverage:** User Stories 8, 9, 10, 11, 12, 16, 17, 19, 20, 36, 37, 39
and 40; ADR-0001.

- [x] Expose rollback through the existing CLI and its established destructive
  confirmation conventions. Refuse unsupported capabilities, unexpected
  topology or conflicting work before unsafe mutation.
- [x] Restore the Snapshot's disk contents and corresponding VM configuration.
  Stopping and restarting the guest is allowed; restoring RAM or process
  execution state is not required.
- [x] Restore coherent libvirt configuration, effective definition, management
  state and cloud-init artifacts/identity. Do not apply clone customization or
  silently reprovision the recovered VM as a new machine.
- [x] Document whether successful rollback consumes or retains the single
  Snapshot. Inspection reports the actual resulting state, the one-Snapshot
  bound is maintained, and the remaining supported lifecycle actions work.
- [x] Check Backup checkpoint implications before rollback. Preserve tracking
  only where valid; otherwise require the documented baseline-reset procedure
  or refuse the unsupported combination. Do not defer this safety condition to
  ticket 07 even though that ticket expands the end-to-end verification.
- [x] Inject failures and interruption at meaningful rollback boundaries.
  Preserve still-needed disk dependencies and management evidence, avoid false
  completion, and document how the operator can safely retry or recover.
- [x] Extend command-boundary tests to cover confirmation/refusal, captured
  configuration and identity, partial failures and the resulting managed state.
  Keep the existing lifecycle failure protections passing.
- [x] On Linux/KVM, capture identifiable guest data and configuration, change
  them, roll back, boot and verify the captured values through the CLI and SSH.
  Check managed status and applicable subsequent VM operations.
- [x] Measure rollback-to-usable-guest duration on the tested configuration and
  record its limitations; a successful hypervisor call alone is not recovery.
- [x] Commit implementation, recovery guidance and passing unit/integration
  evidence before starting ticket 07.
