# 03: Isolate console credentials when cloning VMs

**What to build:** An operator cloning a VM can choose the target's Console
password login policy without sharing the source credential. An enabled target
receives a new credential and a disabled target's management password is locked.
The source remains unchanged, including when clone or credential delivery fails.
This replaces only the temporary clone refusal from ticket 02.

**Blocked by:** 02: Create VMs with explicit console password login.

**Status:** ready-for-agent

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 14, 21-28, 30-38, 40-44, 46; ADR-0003.

- [ ] Use the target definition's explicit policy independently of the source
  policy. Cover enabled-to-enabled, enabled-to-disabled, disabled-to-enabled
  and disabled-to-disabled cloning without weakening existing clone guards.
- [ ] For an enabled target, require a new explicit delivery destination,
  generate a fresh Console credential, and replace inherited password state
  with the new hash for the configured management user. Do not reuse the
  source's plaintext, hash or old delivery destination.
- [ ] For a disabled target, explicitly lock the configured management user's
  password even when the cloned guest contains an existing usable password.
  Never let an omitted new option preserve an inherited unlocked password.
- [ ] Retain target SSH keys, serial console setup and applicable passwordless
  sudo while rejecting SSH password and password-capable keyboard-interactive
  authentication when console opt-in is enabled. Do not enable root passwords,
  autologin or unexpected credential expiration.
- [ ] Apply ticket 02's credential-file permissions, exclusive creation,
  delivery-before-use and no-leakage rules. Validate/dry-run have no credential
  side effects, and a destination alone cannot enable target password login.
- [ ] Failed credential delivery leaves the source and target VM resources
  untouched. Later failures preserve source bytes and management records,
  surviving target resources and credentials needed for their recovery.
  Retry refuses existing credential destinations instead of overwriting them.
- [ ] Remove the now-obsolete console clone refusal without relaxing the
  temporary reinstall refusal or unrelated storage/recovery restrictions.
- [ ] Exercise real clone orchestration with temporary guest artifacts and
  substituted external programs. Use known source credentials to prove a fresh
  target credential, explicit disabled locking, source preservation, preexisting
  account handling and relevant failure behavior. Check synthetic secrets do
  not leak through captured output or managed artifacts.
- [ ] Update clone usage and limitations in both languages. Distinguish host
  provisioning checks from actual old-password rejection, verified in ticket
  06. This ticket can be implemented on bridge/NAT without waiting for ticket 01.
- [ ] Run relevant tests and commit the completed slice before moving on, using
  a Conventional Commit with a detailed body and no co-author trailer.
  Suggested subject: feat: issue independent console credentials for clones.
