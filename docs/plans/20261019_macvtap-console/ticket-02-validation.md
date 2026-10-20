# Ticket 02 validation

Fixed review point: `d1057bb13f666de926e6fef4d7a25abba7fb892e`.
Implementation: `4b8018d`; review refinements: `2c08f1a`.

The first command-boundary tests failed because normalization discarded the
policy and the create command did not accept a delivery destination.
Implementation then made those tests pass. Additional checks cover explicit
policy, rejection of root, previews, exclusive delivery, permissions, ownership,
symlinks, concurrent destination creation, partial writes, synchronization
failure, late creation failure, independent credentials, deletion preservation,
temporary lifecycle refusals and guest SSH hardening before unlock.

Verification:

- Debian 13 authorized host: repository smoke passed, 258 tests passed.
- Real POSIX permissions, ownership, descriptor-relative creation and OpenSSL
  hashing were exercised; virtualization commands were substituted.
- The actual host OpenSSH parser accepted generated global, included and
  Match-scoped settings and reported both password methods disabled for three
  connection addresses. Service restarts and account changes were simulated.
- Windows smoke before review refinements passed: 242 passed, 13 POSIX cases
  skipped. Linux verification covers all review refinements.
- Real guest boot, console login, SSH key login and authentication rejection
  remain ticket 06. These fast tests do not establish those outcomes.

## Standards review

No blocking documented-standard violations. The advisory Mysterious Name finding
was addressed by renaming the helper to `deliver_console_credential`.

## Spec review

No must-fix findings. Initial password locking followed by unlock after SSH
hardening was accepted as a security-preserving implementation refinement.
Advisory test gaps were addressed with actual OpenSSH parser checks,
foreign-owned directory rejection and partial-write failure coverage.

Focused follow-up review of `4b8018d...2c08f1a` passed on both axes with no unresolved must-fix findings. The ticket review gate is closed.
