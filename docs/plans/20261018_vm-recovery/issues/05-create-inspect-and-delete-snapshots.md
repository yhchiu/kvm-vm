# 05: Create, inspect and delete one short-lived Snapshot

**What to build:** An operator can create one Snapshot of a running VM, inspect
its recovery point, and remove it after a successful change while keeping the
current guest data. The workflow owns its disk dependencies and failure handling
from the first usable version; rollback is delivered separately in ticket 06.

**Blocked by:** 04: Restore incremental Backups and establish the next baseline.
This is the accepted ADR-0001 recovery-validation gate as well as the source of
the backup compatibility baseline.

**Status:** complete; see the [validation record](../ticket-05-validation.md).

**Spec coverage:** User Stories 2, 3, 4, 5, 6, 7, 13, 14, 15, 16, 17, 18, 19,
20, 21, 22, 23, 24, 26, 39 and 40; ADR-0001.

- [x] Expose creation, inspection and deletion through the existing CLI
  architecture, using libvirt and system tools. Establish and document the
  supported capability/storage matrix before mutation; reject unsupported
  coverage or unexpected topology explicitly.
- [x] Create without shutting down or rebooting the guest. Brief filesystem
  freeze or I/O pauses are permitted; RAM and process-state capture are outside
  scope. Record the corresponding disk, VM configuration and management
  artifacts coherently for later rollback.
- [x] Keep at most one Snapshot per VM. A second creation is rejected without
  changing the existing recovery point, and inspection clearly distinguishes
  no Snapshot, a usable Snapshot and incomplete work requiring attention.
- [x] Persist enough information to identify the VM/recovery point, track disk
  dependencies and explain incomplete operations. Existing managed VMs without
  Snapshot metadata remain usable.
- [x] Delete the Snapshot while retaining current guest writes. Remove only
  artifacts proven unnecessary to the resulting disk topology; verify that a
  subsequent Snapshot can be created after successful cleanup.
- [x] Coordinate conflicting kvm-vm operations, check active disk jobs and
  document exclusions for direct external tools. Existing clone, reinstall,
  delete and keep-storage behavior must safely handle or explicitly refuse the
  newly introduced managed Snapshot state from the moment creation is enabled.
- [x] When creation freezes the guest, exercise failure after freeze and the
  thaw path. Failed thaw or uncertain completion must be reported with recovery
  guidance, not hidden behind success. Interrupted creation/deletion preserves
  recoverable dependencies and provides a documented safe retry/recovery path.
- [x] Do not silently accept unverified Backup checkpoint combinations. Keep
  conservative refusals explicit while preserving existing data and tracking;
  broader interoperability is verified in ticket 07.
- [x] Use command-boundary tests for the one-Snapshot limit, absent and partial
  state, topology mismatch, unsupported capabilities, conflicts and failures.
  Assert guest/data and artifact outcomes rather than internal helper ordering.
- [x] On Linux/KVM, create while a guest runs, prove it did not reboot, make
  identifiable writes, delete the Snapshot and verify those writes survive.
  Inspect storage dependencies and exercise applicable lifecycle operations.
- [x] Record supported configurations and measured creation pauses without
  claiming a numerical SLA. Commit implementation, operator documentation and
  passing unit/integration evidence before ticket 06.
