# 06: Verify console authentication and credential lifecycle on KVM

**What to build:** Maintainers can demonstrate on supported guest images that
Console password login works when enabled, the locked default remains intact,
SSH continues to require keys, and clone/reinstall issue independent credentials.
The acceptance workflow covers bridge, NAT and Macvtap without interpreting a
network timeout as an authentication rejection or console success as LAN proof.

**Blocked by:** 01: Support the single-NIC Macvtap lifecycle;
03: Isolate console credentials when cloning VMs;
04: Preserve console policy and rotate credentials on reinstall.

**Status:** implemented; review pending; full acceptance pending compatible hardware

The operator confirmed no newer KVM host is available. Retain Rocky Linux 9/10
and AlmaLinux 9/10 as hardware-limited acceptance cases on the Xeon L5420;
do not infer their console results from the CPU-compatible image subset.

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 20-48; ADR-0002 and ADR-0003.

- [x] Provide an opt-in real-CLI and serial-console workflow following existing
  E2E resource-ownership conventions. Record the supported image/cloud-init/
  password-stack matrix and exercise console opt-in independently across
  bridge, NAT and Macvtap. Ticket 05 is not a prerequisite; supply the reachable
  network client needed for authentication checks directly.
- [ ] For each supported image, inspect relevant serial hardware/getty setup,
  then use the delivered credential to log in as the configured management
  user and exercise sudo. Include images with a preexisting account. Verify
  there is no forced first-login password change or unexpected expiration.
- [x] Show that the default policy remains locked, including when Macvtap is
  selected. No console autologin or newly enabled root password login is
  introduced. A blank/unavailable console is a failure or pending prerequisite,
  not proof that the password was correctly rejected.
- [x] From a client known to reach the guest SSH service, prove key login
  succeeds while password and password-capable keyboard-interactive login
  fail. Check effective SSH policy where needed, including conditional
  configuration. A network timeout cannot satisfy a negative authentication
  test. Redact credentials and secret-bearing serial interaction.
- [x] Verify credential-file owner, permissions and delivery sequencing on
  Linux. Inspect ordinary output, logs and managed provisioning artifacts for
  accidental plaintext using synthetic test credentials; do not publish those
  secrets in assertion failures or evidence.
- [x] Clone an enabled source to enabled and disabled targets. Prove the
  source still accepts its own credential, the enabled target accepts only its
  new credential, and the disabled target rejects the inherited password.
  Cover enabling a target from a disabled source as well.
- [x] Reinstall enabled and disabled guests. Prove policy preservation,
  acceptance of the new credential only when enabled, rejection of the old
  credential after enabled reinstall, and continued key-only SSH. Repeat
  applicable login and policy checks after reboot.
- [x] Run the protected-delivery and lifecycle failure tests from tickets
  02-04 on the required POSIX environment, including existing/symlink
  destinations, delivery errors, partial provisioning and retries. Record
  credential and VM preservation outcomes without claiming a rollback of an
  already destructive replacement.
- [x] Verify deletion leaves an operator-owned credential file available for
  explicit cleanup. Document that guest-side password changes make the initial
  file stale, and that missing credentials or a guest unable to reach login
  still require another authorized recovery path.
- [x] Test-owned cleanup must preserve recoverable data when it cannot finish
  safely. Use unique guest identities and private credential destinations;
  ensure normal test logs and diagnostics remain redacted.
- [x] Record per-image/per-mode/lifecycle pass, fail or pending results and
  repeatable invocation guidance. Generated YAML, mocked processes and a subset
  of working images cannot establish the remaining acceptance matrix. Keep
  this ticket open when required hardware, images or client access are missing.
- [x] Commit validated harness/evidence work using a Conventional Commit with
  a detailed body and no co-author trailer, while keeping any incomplete
  acceptance explicit. Suggested subject: test: verify console authentication
  and credential rotation on KVM.

Validation, exact pass/pending boundaries, raw failure reports and completed
results: [ticket-06-validation.md](../ticket-06-validation.md).
