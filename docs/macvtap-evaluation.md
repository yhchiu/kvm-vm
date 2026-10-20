# Macvtap support evaluation

Status: accepted design; implementation and real-host acceptance are pending.
The decision records are [ADR 0002](adr/0002-macvtap-attachment-boundary.md) for
network attachment and [ADR 0003](adr/0003-explicit-console-password-authentication.md)
for optional console authentication. This document holds supporting research,
configuration details and the implementation/validation plan.
Reviewed on 2026-10-19. No Linux/KVM execution was performed for this evaluation.

Confirmed scope: research and design only, with no implementation in this task.
The requested outcome is a VM with its own IP on the physical LAN, without
reconfiguring the host NIC into a Linux bridge.
Network management originates from another LAN computer. Host-local management
uses serial console with explicitly enabled password login; no second management
NIC is added. A network choice does not automatically enable password login.

## Assessment

Adding a single macvtap NIC is technically feasible without replacing the
existing provisioning flow. Direct attachment alone does not provide the
host-to-guest network path. Optional console authentication supplies a host-local
login path while retaining a single NIC; it requires credential lifecycle and
guest-image validation beyond the network attachment change.

## Verified external facts

- Libvirt supports Linux macvtap through `interface type='direct'` and a named
  source interface. Explicit `mode='bridge'` permits local guest-to-guest
  delivery when both endpoints use that mode. VEPA relies on the external
  switch for this delivery; private mode requires an external router/gateway.
  Libvirt also documents passthrough, with device-specific requirements.
  [Libvirt domain XML](https://libvirt.org/formatdomain.html#direct-attachment-to-physical-interface).
- Macvtap bridge mode is distinct from a Linux bridge. The guest can reach the
  LAN while communication with its host through the parent interface remains
  unavailable by design. Libvirt documents a second NIC on an isolated network
  as a workaround. Adding that NIC would expand this project's current model.
  [Libvirt troubleshooting](https://wiki.libvirt.org/TroubleshootMacvtapHostFail.html).
- The existing dependency supports
  `--network type=direct,source=IFACE,source.mode=bridge,model=virtio,mac=MAC`.
  Repeating `--network` adds NICs.
  [Upstream virt-install manual](https://github.com/virt-manager/virt-manager/blob/main/man/virt-install.rst).
- A VLAN source such as `eth0.2` appears in libvirt's direct-attachment example.
  Generic guest-transparent `<vlan>` tagging is not supported for ordinary
  macvtap bridge mode; the documented direct-interface exception is SR-IOV
  passthrough. A preconfigured VLAN parent is therefore a candidate to validate,
  not a reason to add arbitrary VLAN XML.
  [Libvirt domain XML](https://libvirt.org/formatdomain.html#setting-vlan-tag-on-supported-network-types-only).
- `virsh domifaddr` reads guest-agent information, DHCP leases, or the host ARP
  table depending on its source. Discovery and actual network reachability are
  different checks.
  [Virsh manual](https://libvirt.org/manpages/virsh.html#domifaddr).

## Repository findings and implications

These are observations from [kvm-vm](../kvm-vm), not upstream guarantees.

- `normalize_definition` accepts only `bridge` and `nat`. `KVM_VM_BRIDGE`
  explicitly changes the mode to bridge. The agreed new policy rejects it when
  YAML selects macvtap, while preserving existing bridge/NAT behavior.
- `validate_network_attachment` and `define_vm` use bridge-versus-else dispatch.
  Both need an explicit macvtap branch; otherwise it will be treated as NAT.
- Create, clone and reinstall share these helpers. Saved normalized definitions
  must retain the parent and attachment mode so subsequent operations preserve
  the chosen topology. Cross-host recovery needs a valid destination parent.
- Reinstall temporarily removes the existing configuration override variables
  before normalization. A new macvtap variable must be considered explicitly
  here; otherwise a shell setting could silently change the saved topology.
- `make_cloud_files` generates one MAC-matched `eth0` configuration. Its DHCP
  and static addressing can plausibly be reused for one macvtap NIC. A second
  management NIC requires a broader schema, addressing and route design.
- `get_ips` tries agent, lease and ARP sources. For external LAN DHCP, do not
  promise discovery through libvirt leases; agent availability should be tested.
  Cloud-init optionally installs/enables the agent; `define_vm` does not explicitly
  configure its channel, so verify generated XML and runtime agent operation.
- Lifecycle commands do not wait for SSH. The host-originated SSH assumptions
  live in [the NAT E2E suite](../tests/test_e2e.py); reusing that suite unchanged
  would not establish macvtap support. An external LAN test client is needed.
- `make_cloud_files` locks the management user's password and disables SSH
  password authentication. `define_vm` supplies a serial console device and
  `cmd_console` attaches through virsh, but neither ensures a usable guest login.
- The management user has `sudo: ALL=(ALL) NOPASSWD:ALL`. Its console password
  therefore permits VM administrator access. The existing README description of
  `disable_root` is too broad: cloud-init's SSH root policy must not be presented
  as a universal prohibition on local root authentication.

## Confirmed initial boundary and YAML interface

The support boundary and rationale are recorded in [ADR 0002](adr/0002-macvtap-attachment-boundary.md).
This research does not establish bond, VLAN, Wi-Fi or nested-host compatibility.

Use `network.mode: macvtap` plus `network.parent: eno1`; the macvtap submode is
fixed to bridge and is not a user setting. Reuse DHCP/static IP configuration.
Preserve the existing bridge default. Validate the parent
before creating VM artifacts; validation must not reconfigure the host.

```yaml
network:
  mode: macvtap
  parent: eno1
  ipv4:
    method: dhcp
```

`KVM_VM_BRIDGE` conflicting with macvtap must produce a clear error rather than
silently selecting a Linux bridge.

## Confirmed environment override behavior

The environment interface is:

- `KVM_VM_MACVTAP=eno1` selects macvtap and overrides the parent, including when
  the input YAML selects bridge or NAT. An explicitly empty value is invalid.
- Setting both `KVM_VM_MACVTAP` and `KVM_VM_BRIDGE` is an error. With neither set,
  use YAML as usual. Existing IP, gateway and DNS overrides remain applicable.
- Save the effective macvtap mode and parent so later operations do not require
  the original environment variable.

Apply the override to validate/create/clone, while reinstall
ignores it, just as it ignores the current network overrides, and uses the saved
effective definition. Temporarily suppress the new variable during reinstall
normalization and restore the process environment afterward.

The single variable mirrors the existing bridge override and avoids partial
mode/parent settings.

## Console authentication design

[ADR 0003](adr/0003-explicit-console-password-authentication.md) owns the
authentication decision. Console password login is an explicit opt-in applicable
to all network modes; the existing locked-password behavior remains the default.
Exact user-facing option names are an implementation detail, not existing CLI
or YAML support. Do not infer opt-in from `KVM_VM_MACVTAP`.

### Provisioning and SSH separation

- Generate at least 24 random characters from an unambiguous console-friendly
  alphabet using a cryptographically secure generator. Use an independent value
  for each VM; do not derive it from VM names, timestamps or a shared seed.
- Supply a salted hash through `hashed_passwd`, including for an account already
  present in the image. Initially retain `lock_passwd: true`; unlock the account
  only after SSH password methods have been disabled and checked and the serial
  getty has started. This ordering avoids a temporary SSH password-login window.
  Supplying the hash must not depend on downloading packages inside a guest
  whose network may need repair.
- Keep `ssh_pwauth: false`. Verify the effective OpenSSH configuration disables
  both password and password-capable keyboard-interactive authentication,
  including relevant Match blocks and distro configuration. Preserve key login.
- Do not set a root password or enable autologin. Verify the guest has a working
  serial getty on the configured console; virtual hardware alone is insufficient.
- Do not expire the generated password or require a first-login change by
  default. This is a recovery credential; rotation is explicit, and expiry can
  affect non-console authentication too.

Cloud-init documents [hash and lock semantics](https://docs.cloud-init.io/en/latest/reference/modules.html#users-and-groups)
and [random passwords printed to console](https://docs.cloud-init.io/en/latest/reference/modules.html#set-passwords).
OpenSSH exposes separate authentication controls in
[sshd_config](https://man.openbsd.org/sshd_config); guest getty activation is
described by [systemd-getty-generator](https://www.freedesktop.org/software/systemd/man/latest/systemd-getty-generator.html).
These are upstream capabilities, not evidence that every supported image has
been tested. Verify cloud-init versions and password-hash support per image.

### Credential delivery and lifecycle

The initial delivery contract is an explicitly selected host credential file,
owned by the invoking administrator, with mode 0600 in a private directory.
Use exclusive creation, reject existing destinations and symlinks, and verify
the destination before VM mutation. Never write plaintext to ordinary YAML,
state JSON, seed/user-data, logs, command arguments or normal stdout. The
credential file is the intentional plaintext exception and must be treated as
a secret; operators may transfer it to a password manager.

Write the credential securely before a VM can start using it. Keep it if a
later step fails and a surviving VM or disk might use that password; do not
overwrite it on retries. Report its location without printing its contents.
Validation and dry-run must neither generate credentials nor create secret
files. Restrict access to password hashes, user-data and seed artifacts too.
Store policy in the effective definition, not a recoverable plaintext password.

| Operation | Required behavior |
| --- | --- |
| Create with opt-in | Generate and securely deliver a new credential, then provision its hash. |
| Clone with opt-in | Generate a new credential; overwrite inherited password state rather than reuse the source password. |
| Clone without opt-in | Explicitly lock the target user's password, including passwords inherited from the source. |
| Reinstall | Preserve the saved enabled/disabled policy; when enabled, generate and deliver a new credential because the guest OS is replaced. |
| Guest-side password change | The delivered initial password becomes stale; do not claim that the host file tracks subsequent changes. |
| Lost credential | A hash cannot reveal the password. Use another authorized access path or an offline reset; no automatic console bypass. |
| Delete | Do not implicitly delete an operator-selected credential file outside managed artifacts; document operator cleanup responsibility. |

The implementation must distinguish recording credentials from successful guest
provisioning. A successful create command alone does not prove cloud-init has
completed or that console authentication works. Existing VMs are not retrofitted
by merely editing their saved definition.

## Deployment acceptance prerequisites

1. Supply another LAN computer for network acceptance and host console access
   for optional password-login acceptance. Console tests do not prove LAN access.
2. Initial support: an existing wired Ethernet parent, one VM NIC,
   and macvtap bridge mode. Bond/VLAN/Wi-Fi remain outside initial acceptance.
3. Verify LAN DHCP availability when using DHCP, or supply valid static settings.
   Verify that the switch permits the guest MAC addresses on the parent port.
   These checks belong to actual deployment acceptance; no remote host access
   or deployment-specific compatibility has been established in this research.

## Validation and possible commit split

The present change is documentation only; code tests do not validate these claims.
If implementation is authorized, split work into these commits, validating and
committing each task before moving to the next:

1. Macvtap schema, override precedence, parent validation, command generation,
   lifecycle preservation, unit coverage and both-language network documentation.
2. Explicit console-password policy, secure generation/delivery, cloud-init and
   SSH configuration, clone/reinstall handling, unit/integration coverage and
   both-language credential/recovery documentation. Correct the existing overly
   broad root-login wording as part of this feature.
3. Opt-in Linux/KVM acceptance harness and recorded results, using serial console
   for authentication checks and another LAN client for Macvtap connectivity.

Required checks: regress bridge/NAT behavior; reject missing/invalid parents
before side effects; verify emitted domain XML; test DHCP and static networking,
guest-agent IP discovery, LAN SSH, guest-to-guest traffic, the expected host-path
limitation, and create/clone/reinstall/delete. Confirm restart behavior and that
deletion leaves the host parent unchanged. VLAN/bond support requires its own
real-host matrix. No tests or performance measurements have been claimed here.

Console tests must cover the locked default across all network modes, fresh
credentials on clone/reinstall, dry-run without secrets, exclusive file creation,
permissions, refused symlink/existing destinations, failures before and after
VM definition, and absence of plaintext from captured output and managed files.
Use synthetic credentials in tests and redact all authentication transcripts.
On each supported image, prove serial login and sudo, successful SSH key login,
and rejected SSH password/keyboard-interactive password login. Reboot and repeat
the relevant checks; verify that passwords are not unexpectedly expired and
that cloned accounts do not retain the source password. Failed or unrun checks
remain pending acceptance rather than being inferred from generated YAML.
