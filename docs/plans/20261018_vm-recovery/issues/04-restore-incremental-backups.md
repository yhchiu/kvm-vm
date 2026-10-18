# 04: Restore incremental Backups and establish the next baseline

**What to build:** An operator can recover the intended point from a full plus
incremental Backup history, retain matching kvm-vm management information, and
take subsequent Backups from a valid baseline. Broken or incomplete tracking
must not be reported as a successful continuation of the old history.

**Blocked by:** 03: Restore a full Backup on a replacement host.

**Status:** ready-for-agent

**Spec coverage:** User Stories 27, 29, 34, 35, 36, 37 and 40; ADR-0001.

- [ ] Extend the external-tool procedure with full and incremental Backup
  creation, the material needed to reconstruct the chosen point, and the
  distinction between Backup checkpoints and recoverable Backup data.
- [ ] Include management artifacts appropriate to the chosen recovery point.
  Demonstrate that later definition changes are not accidentally paired with
  earlier disk contents during Restore.
- [ ] On Linux/KVM, create a full Backup and subsequent identifiable guest
  changes and incremental Backups. Restore the selected point on a distinct
  compatible host using independently retained recovery material.
- [ ] Verify the selected guest data, configuration, intended identity and
  continued kvm-vm management using the acceptance workflow from ticket 03.
- [ ] Document and test how the restored VM starts its next valid Backup
  history. If tracking cannot be continued, explicitly establish a new full
  baseline instead of treating incompatible checkpoints as usable.
- [ ] Exercise missing or invalid checkpoint/bitmap state and an incomplete
  Backup history. The workflow must produce an actionable refusal or an
  explicitly reported full-baseline replacement, not false incremental success.
- [ ] Verify subsequent Backup data by restoring and checking guest-visible
  contents; external command exit status alone is not acceptance evidence.
- [ ] Extend existing command-boundary tests for any integration changes and
  keep external backup tooling optional for unrelated VM operations.
- [ ] Record the tested tool versions, supported sequences and real-machine
  results. Missing acceptance prerequisites leave this ticket pending.
- [ ] Commit the verified workflow, tests and evidence before ticket 05. This
  completes the backup recovery validation gate required before Snapshot work.
