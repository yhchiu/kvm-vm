---
status: accepted
date: 2026-10-19
---

# Make console password authentication explicit and independent of networking

Serial console is the chosen host-local management path for single-NIC Macvtap
VMs, but the current locked user password prevents ordinary console password
login. We will offer opt-in random-password authentication for the configured
management user, independently of bridge, NAT or Macvtap attachment. Passwords
remain locked by default. Selecting Macvtap must not silently change account
authentication policy.

## Decision and alternatives

When enabled, generate a unique password using a cryptographically secure
random source on the host, unlock the management user's password, and provision
only its supported salted password hash through cloud-init. Keep SSH key
authentication and disable SSH password and password-capable keyboard-interactive
authentication. Do not enable root password login or console autologin.
Unlocking the user password is a local account policy, not a restriction that
limits that password to the serial device.

Deliver the password through an explicitly selected, access-restricted credential
destination, separate from normal output, logs, definitions and state. A hash
cannot recover the original password. Clone gets a fresh credential; reinstall
preserves the enabled/disabled policy but issues a fresh credential when enabled.
Do not impose password expiration or first-login password change by default for
this recovery use case. Future expiration support needs explicit policy and
tests for its effect on key-based SSH and automation.

Keeping all passwords locked avoids credential custody but requires another
access path or an offline reset when console login is needed. Automatically
unlocking every Macvtap VM couples authentication to an unrelated network choice.
Cloud-init's built-in random-password mode prints the password to the system
console, making delivery depend on boot output and its retention. Explicit host
generation and credential delivery provide a clearer ownership boundary.

## Consequences

Operators gain network-independent console login and must protect the credential.
The existing management user has passwordless sudo, so possession of its console
password effectively grants VM administrator access. Seed files and password
hashes still require restricted access. Host/libvirt console authorization,
guest serial getty and guest authentication are separate prerequisites.

This path cannot repair a guest that cannot reach a working login service or
whose initial account provisioning never completed. Each supported image must
prove console login, successful SSH key login and rejected SSH password and
keyboard-interactive password login. Credential delivery and clone/reinstall
behavior also need failure-path validation.

This is an accepted design, not an implemented feature. See the
[evaluation](../macvtap-evaluation.md#console-authentication-design) for credential
handling and tests, and cloud-init's
[users module](https://docs.cloud-init.io/en/latest/reference/modules.html#users-and-groups)
and [password module](https://docs.cloud-init.io/en/latest/reference/modules.html#set-passwords)
for provisioning semantics.
