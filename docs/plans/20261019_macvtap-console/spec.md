---
status: ready-for-agent
decisions:
  - ADR-0002
  - ADR-0003
---

# Single-NIC Macvtap and explicit serial console authentication

## Problem Statement

VM operators need guests with their own addresses on the physical LAN without
moving the host's physical interface or IP configuration into a Linux bridge.
kvm-vm currently accepts only bridge and NAT attachment. Simply passing a
physical interface as a bridge does not provide Macvtap attachment.

A Macvtap attachment does not provide direct communication between its guest
and the host through the Parent interface. Operators accept managing the guest
over SSH from another LAN computer, but need a host-local management path that
does not depend on guest network reachability. Adding a second management NIC
would introduce another network, address and routing policy that this initial
feature does not need.

kvm-vm already exposes a serial console device and an attach command, but locks
the configured management user's password. Connecting to the console therefore
does not provide ordinary password login. Providing a usable Console password
login requires explicit account policy, secure credential delivery and
verification that the guest can actually present and authenticate a login.

## Solution

Offer a single Macvtap NIC attached to an existing wired Ethernet Parent
interface, using Macvtap bridge mode. Operators select it in their definition
or through one environment override. kvm-vm validates the attachment without
reconfiguring host networking and preserves the effective topology for later
lifecycle operations. Existing bridge and NAT workflows remain available.

Keep network management on the physical LAN and use serial console for local
management. Console password login is explicitly enabled independently of
network mode. When enabled, generate a unique Console credential for the
configured management user, deliver it through a protected host file, and
provision only its hash into the guest. Keep SSH key login and reject SSH
password authentication. Do not automatically enable password login merely
because the VM uses Macvtap.

Retain the existing locked-password default. Clone receives a new credential;
reinstall preserves the saved authentication policy but generates a fresh
credential when that policy is enabled. Document that serial console requires
a functioning guest login service and is not a guarantee of recovery from any
guest boot or provisioning failure.

## User Stories

1. As a VM operator, I want to select Macvtap attachment, so that my VM can have its own address on the physical LAN.
2. As a host administrator, I want to use an existing wired Parent interface, so that creating a VM does not require moving the host IP into a Linux bridge.
3. As a host administrator, I want host interface creation and configuration to remain my responsibility, so that kvm-vm does not change shared networking unexpectedly.
4. As a VM operator, I want Macvtap bridge mode to be explicit in the resulting attachment, so that compatible guests on the same parent can communicate locally.
5. As a VM operator, I want one network interface in the initial Macvtap design, so that I do not need a separate management subnet or dual-interface routing policy.
6. As a VM operator, I want existing DHCP and static address settings to work with Macvtap, so that I can use the addressing policy of my LAN.
7. As a VM operator, I want existing address, gateway and DNS overrides to remain usable, so that changing attachment does not require a new addressing interface.
8. As an automation author, I want one environment variable to select Macvtap and its parent, so that I can reuse an existing bridge or NAT definition.
9. As an automation author, I want an empty Macvtap override rejected, so that a missing variable value cannot silently choose another attachment.
10. As a VM operator, I want conflicting bridge and Macvtap settings rejected clearly, so that an exported variable does not silently attach the VM to the wrong network.
11. As a VM operator, I want missing or unsupported Parent interfaces rejected before VM mutation, so that an invalid attachment does not leave partial VM resources.
12. As a VM operator, I want validation and dry-run to show the effective network settings, so that I can review override precedence before creating a VM.
13. As a VM operator, I want the effective parent and attachment saved, so that subsequent commands do not depend on my original shell environment.
14. As a VM operator, I want clone to use the target's selected network configuration, so that the clone reaches the intended LAN.
15. As a VM operator, I want reinstall to keep the saved network despite ambient overrides, so that rebuilding the OS does not silently change connectivity.
16. As a host administrator, I want VM deletion to leave the Parent interface unchanged, so that deleting one guest does not disrupt other guests or the host.
17. As an existing user, I want bridge and NAT behavior preserved outside the new explicit conflicts, so that this feature does not break current definitions.
18. As a VM operator, I want the Macvtap host-path limitation documented, so that I do not mistake it for a guest network failure.
19. As a VM operator, I want SSH from another LAN computer to remain the network management path, so that I can manage the guest without another VM NIC.
20. As a VM operator, I want serial console to provide a host-local login path when enabled, so that I can diagnose guest network problems without LAN access.
21. As a VM operator, I want Console password login to require explicit opt-in, so that choosing a network mode does not change my authentication policy.
22. As a bridge or NAT user, I want the same console opt-in available, so that local management is not tied to Macvtap.
23. As a VM operator, I want the password locked when console opt-in is absent, so that existing key-oriented provisioning remains the default.
24. As a VM operator, I want each enabled VM to receive a cryptographically generated password, so that guests do not share predictable credentials.
25. As a VM operator, I want a password that can reasonably be typed at a serial console, so that emergency login does not depend on copying difficult characters.
26. As a VM operator, I want the credential assigned to the configured management user, so that I can use the established account and sudo workflow without enabling root password login.
27. As a security administrator, I want guest provisioning to contain a supported salted password hash, so that it does not retain the plaintext Console credential.
28. As a security administrator, I want SSH key login retained and SSH password and password-capable keyboard-interactive login rejected, so that enabling local authentication does not enable remote password access.
29. As a VM operator, I want the guest's serial login service verified, so that a configured virtual device is not mistaken for a usable console.
30. As a VM operator, I want credentials delivered to a destination I explicitly selected, so that I know where to retrieve and protect them.
31. As a security administrator, I want the credential file restricted to its administrator owner, so that unrelated host users cannot read it.
32. As a VM operator, I want existing or symlink credential destinations rejected, so that a retry cannot overwrite another secret or redirect its delivery.
33. As an automation author, I want passwords excluded from normal output, logs, definitions, state and command arguments, so that normal operational records do not become credential stores.
34. As a VM operator, I want credential delivery completed before the new guest can use the password, so that I do not create a VM whose initial credential was never delivered.
35. As a VM operator, I want credentials retained after a partial failure when surviving resources may use them, so that retry or recovery does not destroy the only delivered password.
36. As an automation author, I want validation and dry-run to create no credentials or secret files, so that inspection has no credential side effects.
37. As a VM operator, I want clone to generate a fresh Console credential, so that source and target do not share the initial password.
38. As a VM operator, I want a clone without console opt-in to lock the target management password, so that an inherited source password does not bypass the target policy.
39. As a VM operator, I want reinstall to retain console opt-in but deliver a new credential, so that the new OS has an intentional and available login credential.
40. As a VM operator, I want the initial Console credential not to expire automatically, so that recovery access does not depend on an unplanned password-change interaction.
41. As a VM operator, I want documentation to explain that guest-side password changes make the delivered file stale, so that I do not treat it as a live password database.
42. As a VM operator, I want a lost-password recovery procedure, so that I understand a stored hash cannot reveal the original password.
43. As a VM operator, I want externally selected credential files preserved when a VM is deleted, so that VM cleanup does not remove operator-owned files unexpectedly.
44. As a VM operator, I want the privilege of the Console credential explained, so that I protect a password that effectively grants administrator access through sudo.
45. As a maintainer, I want network and authentication acceptance tested separately, so that successful console login does not conceal broken LAN access.
46. As a maintainer, I want failures exercised through the existing command interfaces, so that tests verify preservation and observable outcomes rather than internal helper structure.
47. As a maintainer, I want acceptance results recorded for supported guest images, so that generated configuration is not treated as proof of working authentication.
48. As an operator restoring onto another host, I want the required Parent interface and credential limitations documented, so that I can reconcile the destination environment deliberately.

## Implementation Decisions

These decisions implement ADR 0002 and ADR 0003. They describe the accepted
behavior; neither Macvtap nor optional console passwords are implemented yet.

1. **Architecture and ownership.** Extend the existing CLI, configuration
   normalization, attachment validation, domain definition and cloud-init
   provisioning flow. Do not introduce a second management NIC, network
   controller or new public testing API. Operators own host network setup.

2. **Attachment contract.** Accept network.mode as macvtap with a nonempty
   network.parent naming an existing wired Ethernet interface. Keep bridge as
   the existing default. Macvtap bridge mode is fixed rather than a user-visible
   submode. The generated guest has one direct Macvtap interface with the
   requested parent, model and MAC. Existing IPv4/IPv6 configuration behavior
   is reused; this work does not expand the supported addressing feature set.

3. **Override precedence.** KVM_VM_MACVTAP selects Macvtap and overrides the
   parent even if the input definition selects bridge or NAT. An explicitly
   empty value is invalid. Setting both KVM_VM_MACVTAP and KVM_VM_BRIDGE is an
   error. KVM_VM_BRIDGE also conflicts with a definition that selects Macvtap.
   Without either variable, honor the definition and existing defaults.
   Preserve current address, gateway and DNS overrides and existing bridge/NAT
   precedence outside these new conflicts.

4. **Preflight and unsupported interfaces.** Validate attachment before creating
   or replacing VM resources. Inspect the supplied interface without bringing
   it up, renaming it, changing its IP or modifying its master. Missing parents,
   unsupported interface kinds and failed inspection must produce actionable
   errors. Do not treat the mere existence of a bridge, VLAN, bond or Wi-Fi
   device as proof that it is a supported Ethernet parent. Local checks cannot
   prove switch MAC policy or external DHCP availability; identify those as
   deployment prerequisites rather than successful preflight guarantees.

5. **Lifecycle persistence.** Persist the effective Macvtap mode and parent.
   Validate/create/clone apply environment overrides. Reinstall suppresses the
   new override along with existing configuration overrides, restores the
   process environment afterward, and uses the saved topology. Delete never
   removes or changes the parent. Preserve existing lifecycle locks, recovery
   guards and surviving-resource failure handling.

6. **Independent authentication policy.** Add explicit console-password opt-in
   for the configured management user across bridge, NAT and Macvtap. Absence
   means password locked. Persist the enabled/disabled policy in the effective
   definition. Selecting Macvtap never implies opt-in. Exact new option names
   were not selected in the design discussion; choose a minimal interface
   consistent with existing command conventions and document it without
   changing this behavioral contract. No new authentication environment
   override is required by this spec.

7. **Password generation and provisioning.** Generate at least 24 characters
   from an unambiguous console-friendly alphabet using a cryptographically
   secure host random source. Provision a supported salted hash and unlock the
   management user's password only when enabled. Apply the credential even if
   the account already exists in the image. Do not use cloud-init's random
   password console output as the delivery mechanism. Account provisioning
   must not depend on an in-guest package download to establish the password.

8. **SSH and local login.** Retain SSH keys, disable SSH password authentication
   and password-capable keyboard-interactive authentication, and verify the
   effective server policy including applicable conditional configuration.
   Do not enable root password login or console autologin. Establish a working
   serial getty for supported guests. Password unlocking affects a local
   account and is not intrinsically restricted to serial access. Preserve the
   existing management user's passwordless sudo and describe its privilege.

9. **Credential delivery.** Require an explicit host credential-file destination
   for operations that will issue an enabled credential. Use exclusive creation,
   administrator ownership, mode 0600 and a private containing directory.
   Reject existing destinations and symlinks. Validate the destination before
   VM mutation and complete secure delivery before the VM can start using the
   password. Close race windows through secure filesystem operations, not a
   separate existence check followed by an overwriting write. Do not silently
   change permissions on an unrelated shared directory to make it acceptable.

10. **Secret boundaries.** The selected credential file is the intentional
    plaintext exception. Never include plaintext in normal stdout, logs,
    command arguments, effective definitions, state or seed/user-data. Restrict
    access to password hashes and seed artifacts too. Report the destination
    and provisioning outcome without its contents. Do not store recoverable
    plaintext merely to support a future password-display operation.

11. **Failure and preview behavior.** Validation and dry-run perform no password
    generation and create no secret files. Credential-delivery failure must
    stop before VM mutation. If a later operation fails after credentials have
    been delivered, retain them when surviving resources may use them and
    explain the outcome. Never overwrite that file during retry. Existing
    lifecycle failure-preservation rules still apply; credential handling must
    not destroy a previous guest on an unsuccessful reinstall.

12. **Credential lifecycle.** Create with opt-in issues a fresh credential.
    Clone with opt-in replaces inherited password state with a new credential;
    without opt-in it explicitly locks the target management password.
    Reinstall retains the saved enabled/disabled policy and issues a fresh
    credential when enabled, requiring a new explicit delivery destination.
    Supplying a destination alone does not enable authentication. Do not
    implicitly replay or overwrite a previously used credential destination.

13. **Expiration, changes and removal.** Do not force expiration or first-login
    change by default. A guest-side password change makes the original file
    stale and is not synchronized back to the host. Lost credentials require
    another authorized path or offline reset. VM deletion does not implicitly
    remove operator-selected credential files outside managed artifacts;
    document operator cleanup responsibility. Changing a saved definition
    alone does not retrofit a running or previously provisioned VM.

14. **Observability and acceptance.** Domain creation success is distinct from
    successful guest provisioning, console login or LAN reachability. Retain
    the existing address discovery behavior without promising external LAN
    DHCP leases will appear in libvirt. Verify guest-agent channel and agent
    operation where relied upon. Console acceptance and LAN acceptance are
    independent requirements, not substitutes for one another.

15. **User documentation.** Document configuration and precedence, parent
    prerequisites, the host-path limitation, console opt-in, credential custody,
    retry/rotation behavior and lost-password recovery in both supported
    documentation languages. Correct wording that treats cloud-init's SSH
    root policy as a blanket prohibition of all local root authentication.

## Testing Decisions

Use the existing command interface as the primary seam. Fast tests invoke real
argument parsing and command handlers with temporary artifacts while replacing
external host programs at their process boundary. Real-host tests invoke the
CLI against libvirt and verify guest behavior. Do not add test-only APIs or
split the feature into many mock-only helper tests.

A good test asserts externally meaningful configuration, output, authentication
or artifact preservation. It can fail when the wrong parent is selected, an old
password survives cloning, plaintext leaks, or a failed operation alters data.
It must not merely restate branches or depend on internal call ordering except
where ordering enforces an observable requirement such as delivery before boot.

Existing prior art includes configuration/override tests, cloud-init artifact
inspection, SSH configuration tests, command-handler lifecycle fixtures with
simulated libvirt and injected failures, and real CLI lifecycle tests using SSH
and temporary VM resources. Reuse these patterns and their isolation practices.

1. **Network behavior.** Cover definition-based and environment-based Macvtap,
   bridge/NAT regressions, empty values, both conflict cases, missing and
   unsupported parents, inspection errors, effective output and persistence.
   Assert invalid preflight preserves VM artifacts and does not change the
   host interface. Verify the resulting direct attachment rather than only
   checking that a command returned success.

2. **Lifecycle integration.** Exercise create, clone, reinstall and delete at
   the command boundary. Prove target clone settings apply, reinstall ignores
   ambient overrides and restores the process environment even on failure,
   and VM deletion leaves host networking intact. Reuse the existing storage
   and interrupted-operation preservation cases.

3. **Authentication artifacts.** Across all three attachment modes, assert that
   the default locks the password and opt-in provisions the correct account
   with a salted hash and an explicit unlocked policy. Confirm SSH key setup
   remains present and password-capable remote authentication is disabled.
   Check that validation/dry-run never calls credential generation or creates
   a secret, and that output does not disclose credentials.

4. **Credential filesystem integration.** On a POSIX filesystem, verify
   ownership, mode and private-directory requirements, exclusive creation,
   existing files, symlinks and concurrent destination creation. Inject write,
   provisioning and domain-definition failures and assert both VM resources
   and delivered credentials are preserved as required. Use synthetic secrets
   to inspect stdout, stderr, logs and managed artifacts for plaintext leaks.
   An exception message must not expose a secret-bearing command or payload.

5. **Credential lifecycle integration.** Prove clone generates a different
   credential and replaces inherited password state, disabled clones remain
   locked, and reinstall preserves policy while issuing a fresh credential.
   Reject accidental reuse of an old delivery file and retain it on relevant
   failures. Verify deletion does not remove an operator-owned credential file.

6. **Real network acceptance.** On a disposable-capacity Linux/KVM host with an
   eligible wired parent, inspect generated domain XML and boot the guest.
   From another LAN computer verify DHCP and static addressing as applicable,
   SSH key login and guest-to-guest traffic. Verify the expected direct host
   path limitation separately. Repeat relevant checks after restart and the
   supported lifecycle operations. Record switch and DHCP prerequisites.

7. **Real console and authentication acceptance.** For each supported image,
   use the serial console to authenticate the management user and exercise
   sudo. From a reachable network client, prove SSH key success and failure
   of password and password-capable keyboard-interactive login. A failed login
   caused merely by network unreachability is not authentication evidence.
   Repeat after reboot, clone and reinstall; verify that the old clone or
   reinstall password is not accepted and no unexpected expiry is imposed.
   Exercise opt-in independently of Macvtap, including bridge and NAT.

8. **Acceptance evidence and hygiene.** Redact credentials from authentication
   transcripts. Test harnesses own only their explicitly created VMs and files
   and must preserve recovery data when cleanup cannot safely finish. Generated
   YAML or mocked commands cannot establish guest compatibility. Missing Linux,
   serial, external-client or image prerequisites are pending acceptance, not
   passing tests. Retain console and offline-recovery limitations in the report.

The operator confirmed this testing seam: the existing command boundary at both
fast and real-host levels, with serial console and a separate LAN client for
acceptance. POSIX credential-file tests validate a required deployment property;
they are not a new production interface.

## Out of Scope

- A second NAT or Isolated management NIC, general multi-NIC schemas, management
  subnet allocation or provisioning of shared libvirt networks.
- Host interface creation, IP migration, automatic host bridge setup or
  automatic VLAN, bond, Wi-Fi and nested-host support.
- Other Macvtap forwarding modes, performance guarantees or changes to the
  existing network-mode defaults.
- Automatically enabling Console password login for Macvtap or changing the
  default locked-password policy for existing workflows.
- Root password enablement, console autologin, SSH password login or removal
  of the existing SSH-key requirement.
- Plaintext credentials in normal output or cloud-init payloads, automatic
  password retrieval from hashes, or synchronization of guest-side changes.
- A password manager integration, general credential rotation service,
  password-expiration framework or new automatic lost-password reset command.
- Retrofitting existing VMs through definition edits alone, changing recovery
  storage safety policies, or guaranteeing console login before the guest has
  a functioning authentication service.
- Claiming deployment acceptance from unit tests alone or doing implementation
  work as part of the current documentation task.

## Further Notes

This local spec is marked ready-for-agent following the project's existing plan
convention. It was synthesized from accepted decisions rather than opening a
new design interview. No issue tracker publication is required or performed.
Accepted design status does not mean runtime support or acceptance is complete.

Deliver implementation in these commits, passing each task's relevant checks
and committing it before starting the next:

1. **feat: add single-NIC macvtap attachment** — configuration, precedence,
   validation, domain generation, lifecycle persistence, fast regression tests
   and usage documentation.
2. **feat: add explicit console password authentication** — policy, secure
   delivery, account/SSH configuration, credential lifecycle, fast and POSIX
   integration tests and credential/recovery documentation.
3. **test: verify macvtap and console authentication on KVM** — opt-in real-host
   and external-client harnesses with recorded results. If infrastructure is
   unavailable, record the unrun matrix explicitly and leave acceptance open.

Do not claim that successful code tests close the real-host acceptance task.
New option spelling and internal decomposition may be selected during
implementation within the contracts above; changes to accepted behavior require
an explicit decision update.

References:

- [Domain glossary](../../../GLOSSARY.md)
- [ADR 0002: Macvtap attachment boundary](../../adr/0002-macvtap-attachment-boundary.md)
- [ADR 0003: Explicit console password authentication](../../adr/0003-explicit-console-password-authentication.md)
- [Research, upstream sources and detailed design](../../macvtap-evaluation.md)
