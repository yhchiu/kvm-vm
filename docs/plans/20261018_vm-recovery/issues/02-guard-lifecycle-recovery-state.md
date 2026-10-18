# 02: Guard lifecycle commands against unsafe recovery state

**What to build:** Operators can use existing lifecycle commands around Backup
checkpoints and unexpected disk chains without silently copying old data or
deleting storage that remains in use. Commands either perform a supported safe
operation or refuse before unsafe mutation and explain the required next step.

**Blocked by:** 01: Preserve VM data when reinstall fails.

**Status:** ready-for-agent

**Spec coverage:** User Stories 17, 20, 21, 22, 23, 24, 26 and 40; ADR-0001.

- [ ] Check the actual domain disk topology and relevant recovery metadata
  before clone, reinstall or delete relies on the state manifest's disk path.
  Untracked or inconsistent topology must not be silently treated as an
  ordinary independent disk.
- [ ] Clone reads current guest data on supported configurations, or refuses
  with an actionable explanation. It never silently clones an outdated base
  image when the guest's active image is elsewhere.
- [ ] Reinstall and delete explicitly handle Backup checkpoints or refuse
  safely. Their behavior explains any consequences for subsequent incremental
  backups instead of silently removing tracking information.
- [ ] Domain-undefinition refusal preserves existing disk and management
  artifacts as established by ticket 01. Cleanup does not remove an image still
  referenced by a domain or dependent disk.
- [ ] Keep-storage preserves the storage dependencies needed to use retained
  data. If a topology cannot be safely supported, refusal leaves it intact.
- [ ] Detect or refuse conflicting lifecycle work and active disk jobs within
  the supported coordination model. Document sequencing for directly invoked
  external tools rather than assuming they participate in kvm-vm coordination.
- [ ] Existing managed VMs without Snapshot metadata still work, and ordinary
  operations do not acquire a mandatory virtnbdbackup installation dependency.
- [ ] Use command-boundary tests with temporary data for checkpoint presence,
  active-path mismatch, dependency preservation and failure/refusal outcomes.
  Retain the data-preservation regressions from ticket 01.
- [ ] Verify the supported behavior on Linux/KVM with a checkpoint and a
  representative external disk chain, recording versions and limitations.
  Explicit refusal is acceptable for unsupported configurations; a skipped
  real-machine check is recorded as pending rather than passed.
- [ ] Document the supported command behavior, run the relevant unit and
  integration checks, and commit this completed ticket before ticket 03.
