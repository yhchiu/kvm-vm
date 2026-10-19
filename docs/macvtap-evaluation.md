# Macvtap support evaluation

Status: research and design finalized. The user confirmed shared understanding
on 2026-10-19, including initial scope, YAML interface, environment override
precedence and reinstall policy. Implementation remains outside this task.
Reviewed on 2026-10-19. No Linux/KVM execution was performed for this evaluation.

Confirmed scope: research and design only, with no implementation in this task.
The requested outcome is a VM with its own IP on the physical LAN, without
reconfiguring the host NIC into a Linux bridge.
The user accepts management from another LAN computer; direct host-to-guest
management is not required for the initial scope, so a second management NIC
is unnecessary.

## Assessment

Adding a single macvtap NIC is technically feasible without replacing the
existing provisioning flow. The main product decision is whether callers need
host-to-guest networking: direct attachment alone does not provide that path.
The initial scope, override precedence and reinstall behavior below are agreed.

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

## Confirmed initial boundary and YAML interface

Support one NIC with an explicit existing wired parent, and macvtap bridge mode
only. Keep host NIC/VLAN creation, host IP changes, a second management NIC,
VEPA/private/passthrough, and performance guarantees outside that initial scope.
Treat bond, VLAN, Wi-Fi and nested-host configurations as separate validation
targets; this research does not establish their compatibility on the user's host.

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

The user confirmed these boundaries and the YAML names in interview Q4-Q6.
`KVM_VM_BRIDGE` conflicting with macvtap must produce a clear error rather than
silently selecting a Linux bridge.

## Confirmed environment override behavior

The user confirmed the following environment override semantics in Q7:

- `KVM_VM_MACVTAP=eno1` selects macvtap and overrides the parent, including when
  the input YAML selects bridge or NAT. An explicitly empty value is invalid.
- Setting both `KVM_VM_MACVTAP` and `KVM_VM_BRIDGE` is an error. With neither set,
  use YAML as usual. Existing IP, gateway and DNS overrides remain applicable.
- Save the effective macvtap mode and parent so later operations do not require
  the original environment variable.

Q8 confirmed: apply the override to validate/create/clone, while reinstall
ignores it, just as it ignores the current network overrides, and uses the saved
effective definition. Temporarily suppress the new variable during reinstall
normalization and restore the process environment afterward.

Separate mode and parent variables were considered; the user chose a single
variable to mirror the existing bridge override and avoid partial settings.

## Deployment acceptance prerequisites

1. Confirmed: management may originate from another LAN computer; a host-to-guest
   management channel is not required initially.
2. Confirmed initial support: an existing wired Ethernet parent, one VM NIC,
   and macvtap bridge mode. Bond/VLAN/Wi-Fi remain outside initial acceptance.
3. Verify LAN DHCP availability when using DHCP, or supply valid static settings.
   Verify that the switch permits the guest MAC addresses on the parent port.
   These checks belong to actual deployment acceptance; no remote host access
   or deployment-specific compatibility has been established in this research.

## Validation and possible commit split

The present change is documentation only; code tests do not validate these claims.
If implementation is authorized, one feature commit can include schema and
environment precedence, validation, command generation, lifecycle preservation,
unit coverage, and both-language usage documentation. A separate acceptance
commit can record Linux/KVM results and an opt-in external-client E2E harness.

Required checks: regress bridge/NAT behavior; reject missing/invalid parents
before side effects; verify emitted domain XML; test DHCP and static networking,
guest-agent IP discovery, LAN SSH, guest-to-guest traffic, the expected host-path
limitation, and create/clone/reinstall/delete. Confirm restart behavior and that
deletion leaves the host parent unchanged. VLAN/bond support requires its own
real-host matrix. No tests or performance measurements have been claimed here.
