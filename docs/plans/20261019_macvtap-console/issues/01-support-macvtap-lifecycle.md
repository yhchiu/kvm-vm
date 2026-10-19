# 01: Support the single-NIC Macvtap lifecycle

**What to build:** Operators can select a Macvtap attachment in their VM
definition or environment, create and clone guests on an existing wired Parent
interface, reinstall without changing the saved network, and delete guests
without altering host networking. The complete path includes validation,
effective configuration, domain creation, lifecycle preservation and usage
documentation. Console passwords remain a separate, disabled-by-default policy.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 1-19, 46, 48; ADR-0002.

- [ ] Accept network.mode as macvtap and a nonempty network.parent. Keep the
  bridge default, fixed Macvtap bridge mode, one guest NIC, and existing model,
  MAC and addressing settings. Do not interpret the parent as a Linux bridge.
- [ ] KVM_VM_MACVTAP selects Macvtap and overrides the parent, including when
  input selects bridge or NAT. Reject an explicitly empty value, simultaneous
  KVM_VM_MACVTAP and KVM_VM_BRIDGE, and KVM_VM_BRIDGE conflicting with Macvtap
  in the definition. Preserve existing address, gateway and DNS overrides and
  bridge/NAT behavior outside these new conflicts.
- [ ] Before creating or replacing guest resources, inspect the parent and
  refuse missing or unsupported interfaces and failed inspection with useful
  diagnostics. Do not accept a bridge, VLAN, bond or Wi-Fi device solely because
  it exists. Do not change the parent name, state, master or IP configuration.
- [ ] Validate and dry-run expose the effective configuration without defining
  a guest. Create produces a direct attachment with the chosen parent and
  Macvtap bridge mode. Persist effective mode and parent for later operations.
- [ ] Clone uses the target's selected attachment and ordinary clone identity
  rules. Reinstall uses saved networking, suppresses ambient overrides and
  restores the process environment on both success and failure.
- [ ] Delete leaves the parent intact. Existing lifecycle serialization,
  storage/recovery guards and failed-operation resource preservation continue
  to apply across create, clone, reinstall and delete.
- [ ] Extend the existing command-boundary tests with temporary artifacts and
  simulated external programs. Verify observable configuration and preservation
  through successful operations, invalid preflight and injected failures, plus
  regression coverage for bridge and NAT. No new test-only API is required.
- [ ] Document setup and override precedence in both documentation languages,
  the lack of a direct host-to-guest path, another LAN client's management role,
  and switch/DHCP prerequisites that local preflight cannot prove. Explain that
  restoring to another host requires a valid destination Parent interface.
- [ ] Do not promise discovery of external LAN DHCP addresses through libvirt
  leases. Keep console authentication unchanged and leave real LAN evidence to
  ticket 05 rather than claiming support from generated configuration alone.
- [ ] Run relevant fast checks and commit the completed slice with a
  Conventional Commit, a detailed body and no co-author trailer before moving
  to another ticket. Suggested subject: feat: add single-NIC macvtap attachment.
