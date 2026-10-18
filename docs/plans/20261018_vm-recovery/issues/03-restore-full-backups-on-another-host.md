# 03: Restore a full Backup on a replacement host

**What to build:** An operator can follow a documented external-tool procedure
to retain a complete recovery set, lose access to the original host, and Restore
a bootable VM that remains managed by kvm-vm on a compatible replacement host.
The deliverable includes the procedure and evidence that it works.

**Blocked by:** 02: Guard lifecycle commands against unsafe recovery state.

**Status:** ready-for-agent

**Spec coverage:** User Stories 1, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 38
and 40; ADR-0001.

- [ ] Document a complete full-Backup and Restore workflow using virtnbdbackup
  and virtnbdrestore directly, with supported host/tool prerequisites and an
  explicit distinction between Snapshot rollback and disaster recovery.
- [ ] Inventory disk data, domain configuration, effective definition, state
  manifest, cloud-init artifacts and identity, plus any firmware or other state
  required by the supported VM configuration. Identify excluded or unsupported
  configurations rather than silently omitting required state.
- [ ] Associate management artifacts with the Backup recovery point and state
  the sequencing or exclusions that prevent incompatible concurrent changes.
  Retain the recovery set independently of the original host and storage.
- [ ] Describe destination compatibility, storage-path mapping, host network
  preparation, permissions and VM name/identity conflicts. Restore preserves
  the intended existing identity instead of using clone or reinstall semantics.
- [ ] Distinguish filesystem consistency from workload-specific application
  consistency. Guest-agent presence does not imply that every application can
  be recovered without its own consistency procedure.
- [ ] Add a repeatable acceptance workflow through the existing CLI and SSH
  test seam. Write identifiable guest data, create the Backup, then perform
  recovery on a distinct compatible destination without access to source-host
  disks, definitions or management artifacts outside the recovery set.
- [ ] Verify Backup integrity and, separately, successful guest boot, expected
  data and identity, managed status and applicable management operations on the
  destination. A checksum, process exit or same-host restore is insufficient.
- [ ] Add a metadata helper only if this exercise demonstrates a concrete
  correctness or operational gap. Record the gap and add command-boundary
  behavior tests for any helper; do not add a wrapper solely for convenience.
- [ ] Record the actual versions, guest/storage configuration, recovery
  artifacts, procedure and results. Missing Linux/KVM hosts or other acceptance
  prerequisites leave this ticket pending; mocked or skipped checks do not
  complete the cross-host recovery gate.
- [ ] Commit the verified procedure, acceptance workflow, any justified helper
  and recorded evidence before starting ticket 04.
