# 01: Preserve VM data when reinstall fails

**What to build:** An operator whose reinstall cannot undefine the existing VM
receives an actionable failure while the original disk, management information
and cloud-init artifacts remain intact. Make the narrow cleanup refactoring
needed to distinguish existing resources from resources owned by a failed
attempt before extending recovery behavior.

**Blocked by:** None (can start immediately).

**Status:** complete

Verification and environment limits: [ticket 01 validation](../ticket-01-validation.md).

**Spec coverage:** User Stories 25 and 40; ADR-0001.

- [x] Reproduce the original failure through the reinstall command handler:
  every attempted domain undefinition fails, including the checkpoint refusal
  case, and the actual cleanup logic executes against temporary resources.
- [x] After failure before replacement, assert that original disk bytes,
  effective definition, state manifest and cloud-init artifacts are preserved.
  The still-defined VM must not reference a disk deleted by error cleanup.
- [x] Remove only temporary resources whose ownership belongs to the failed
  attempt. Cleanup does not infer ownership from a filename or the mere presence
  of an existing VM.
- [x] Distinguish failures before replacement from failures after replacement
  begins; cleanup in either phase must not delete surviving resources merely
  because a later operation failed. This does not promise to undo a successfully
  completed destructive reinstall.
- [x] Preserve the original operation's useful error context and report any
  unresolved cleanup or manual recovery work without claiming success.
- [x] Adding a checkpoint-removal option alone cannot satisfy the regression:
  an arbitrary domain-undefinition refusal must also preserve original data.
- [x] Extend the existing pytest command-handler seam, keeping orchestration
  and cleanup real while substituting external effects. Assert data and
  observable outcomes rather than private helper-call sequences.
- [x] Existing successful reinstall behavior and the disk-creation-failure
  protection test continue to pass. Run the relevant unit checks and document
  the reproduction's mocked-environment limits.
- [x] Commit this completed correction with its regression tests before
  starting ticket 02, using a Conventional Commit with a detailed body and no
  co-author trailer.
