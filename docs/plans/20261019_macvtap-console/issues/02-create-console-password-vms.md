# 02: Create VMs with explicit console password login

**What to build:** An operator explicitly enabling Console password login can
create a VM whose management user receives a new, securely delivered Console
credential, while SSH remains key-only. The locked-password default is retained.
This slice is independently verifiable using existing bridge/NAT attachment and
must not depend on Macvtap. Clone and reinstall combinations that cannot yet
honor the new policy fail before mutation until tickets 03 and 04 add support.

**Blocked by:** None (can start immediately).

**Status:** complete

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 20-36, 40-44, 46; ADR-0003.

- [x] Introduce a minimal explicit console-password opt-in and explicit host
  credential-file destination using existing configuration/CLI conventions.
  Persist the enabled/disabled policy, not plaintext. A destination alone or
  a network mode never enables password login. Without opt-in retain locking.
- [x] Generate at least 24 characters from an unambiguous console-friendly
  alphabet with a cryptographically secure host generator. Assign each created
  guest an independent password; provision only a supported salted hash and
  unlock the configured management user, including an image's preexisting user.
  Do not rely on an in-guest package download to establish this password.
- [x] Retain SSH authorized keys and disable SSH password and password-capable
  keyboard-interactive authentication. Account for effective configuration,
  including applicable conditional settings. Configure a usable serial getty
  for supported guests without enabling root password login or autologin.
  Preserve the existing management user's sudo policy.
- [x] Do not force expiration or a first-login password change by default.
  Explain that unlocking a local password does not intrinsically restrict it
  to a serial device and that its holder gains administrator access via sudo.
- [x] Require delivery to a private host directory and an administrator-owned
  file with mode 0600. Use race-safe exclusive creation, reject existing files
  and symlinks, and do not silently change unrelated directory permissions.
  Validate the destination before VM mutation and deliver the credential before
  the guest can start using it. Delivery failure must leave the VM untouched.
- [x] Never include plaintext in normal stdout, stderr, logs, exception text,
  command arguments, state, effective definitions or cloud-init/seed payloads.
  The explicitly selected credential file is the only intentional plaintext
  artifact. Restrict access to password hashes and provisioning artifacts too.
- [x] Validation and dry-run neither generate passwords nor create credential
  files. Report policy and relevant outcomes without credential contents.
- [x] After a later provisioning/domain failure, preserve delivered credentials
  when surviving resources may use them, preserve resources under existing
  failure rules and report the situation without claiming login success.
  Retries never overwrite an old credential file.
- [x] Keep ordinary legacy lifecycle behavior working. Until tickets 03/04,
  explicitly reject clone involving source or target console opt-in and
  reinstall of a console-enabled VM before mutation. Neither path may silently
  inherit a credential, ignore the policy or provision without delivery.
  These temporary refusals must be covered and documented as incomplete support.
- [x] VM deletion does not implicitly remove an operator-selected credential
  file outside managed artifacts. Document cleanup responsibility, stale initial
  files after guest-side changes, and another authorized path/offline reset for
  lost credentials. Do not add password retrieval or a password manager service.
- [x] Test the real create/validate/dry-run and deletion interfaces with
  temporary artifacts and external-effect substitutes. Use POSIX filesystem
  integration checks for permissions, ownership, exclusive creation, concurrent
  destination creation, symlinks and write failure. Cover preexisting accounts,
  locked defaults, no secret generation in previews, partial failures and
  synthetic-credential leakage through captured outputs and managed artifacts.
- [x] Update both documentation languages, including the distinction between
  SSH root policy and local root authentication. Separate creation success from
  guest provisioning and console login. Real-image authentication evidence is
  completed in ticket 06; Macvtap integration there must not be inferred here.
- [x] Run relevant fast and POSIX checks and commit the completed slice before
  moving on, using a Conventional Commit with a detailed body and no co-author
  trailer. Suggested subject: feat: add explicit console password creation.
