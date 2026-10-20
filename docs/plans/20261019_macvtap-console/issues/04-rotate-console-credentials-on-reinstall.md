# 04: Preserve console policy and rotate credentials on reinstall

**What to build:** Reinstall retains the saved Console password login policy
while issuing a fresh credential for the replacement guest when enabled. The
operator must explicitly choose a new delivery destination. Failures preserve
existing guest resources or surviving replacement resources as appropriate,
without claiming that destructive replacement can always be undone.

**Blocked by:** 02: Create VMs with explicit console password login.

**Status:** complete

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 15, 21-28, 30-36, 39-44, 46; ADR-0003.

- [x] Read the saved enabled/disabled policy rather than inferring it from
  network mode, a credential destination or ambient shell state. Legacy
  definitions without the opt-in remain locked by default.
- [x] Enabled reinstall requires an explicit new host credential destination
  and generates a fresh Console credential and supported hash. Do not replay a
  previously stored output destination or pretend a hash can recover plaintext.
  Disabled reinstall keeps the management password locked without creating a
  credential file.
- [x] Preflight and credential delivery occur before destructive VM mutation,
  including forced shutdown. Missing, existing, symlink or unsafe destinations
  and delivery failures do not change the existing guest or its artifacts.
- [x] Reuse protected exclusive delivery, private-directory and ownership
  checks, no-leakage rules and delivery-before-boot sequencing from ticket 02.
  Validation and dry-run do not generate passwords or write secret files.
- [x] Provision the replacement management account even when the selected
  image already contains it. Retain SSH key login and the console service while
  rejecting SSH password and password-capable keyboard-interactive login for
  enabled policy. Do not introduce root password login, autologin or expiration.
- [x] If failure occurs before replacement, preserve original disk bytes,
  state, effective definition and cloud-init artifacts under existing lifecycle
  rules. After replacement begins, preserve surviving data and the delivered
  credential that may apply to it, accurately reporting the phase and outcome.
  Never delete an old credential file or overwrite it to simplify a retry.
- [x] Preserve saved network settings and existing reinstall environment
  suppression. Do not couple implementation to the clone ticket: both extend
  ticket 02 independently. Replace only the temporary console reinstall refusal.
- [x] Exercise actual reinstall orchestration with temporary artifacts and
  simulated external effects. Cover enabled/disabled and legacy policies,
  preexisting guest accounts, changed images, force, preview, destination
  refusal, write failure, undefinition failure and failures after replacement.
  Verify new credentials, preservation and absence of synthetic plaintext leaks.
- [x] Update both-language reinstall documentation: policy persists but the
  old password does not, the new destination is operator-selected, and guest
  provisioning/login still require verification. Real guest rejection of the
  old credential is part of ticket 06.
- [x] Run relevant checks and commit the completed slice before moving on,
  using a Conventional Commit with a detailed body and no co-author trailer.
  Suggested subject: feat: rotate console credentials during reinstall.
