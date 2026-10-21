# 05: Verify Macvtap networking on a real KVM host

**What to build:** Maintainers can run an opt-in CLI acceptance workflow against
a Linux/KVM host and a separate LAN client, obtaining evidence that single-NIC
Macvtap works through creation, cloning, reinstall, restart and deletion without
changing the host Parent interface. Network acceptance is independent of console
authentication and does not wait for its implementation or validation.

**Blocked by:** 01: Support the single-NIC Macvtap lifecycle.

**Status:** complete

**Spec coverage:** Single-NIC Macvtap and explicit serial console authentication;
User Stories 1-19, 45-48; ADR-0002.

- [x] Provide an opt-in harness using the real CLI and the existing real-host
  testing conventions, with explicit host/parent and external-client inputs.
  Record relevant host, libvirt, tooling, image and network prerequisites without
  credentials. Do not reconfigure a physical NIC to make the test pass.
- [x] Require disposable VM capacity and a supported wired parent. Record
  external DHCP/static-address availability and switch MAC policy assumptions;
  the test must not allocate conflicting LAN addresses or assume local
  interface existence proves upstream compatibility.
- [x] Inspect the defined guest XML and prove a single direct Macvtap NIC with
  the requested parent, bridge forwarding mode, model and MAC. Verify both
  definition-based selection and the environment override behavior.
- [x] Boot guests and verify DHCP and static addressing in the supported
  scenarios. From another LAN computer, prove SSH key login and usable guest
  connectivity; verify guest-to-guest traffic for compatible attachments on the
  same parent. Host console success is not evidence of LAN reachability.
- [x] Check the expected lack of a direct host-to-guest path through the
  Macvtap parent under a documented topology without an alternate route. Do not
  count a generally unreachable guest as proof of the Macvtap limitation.
- [x] Verify address discovery honestly: where guest-agent data is relied
  upon, inspect channel configuration and actual agent operation. Do not
  require external DHCP leases to appear in libvirt or confuse no reported
  address with a failed LAN connection.
- [x] Exercise clone target selection, reinstall with conflicting ambient
  network settings, restart and deletion. Confirm persisted topology and that
  parent addresses, master and operational configuration remain unchanged.
  Existing storage and lifecycle safety restrictions must remain enforced.
- [x] Own only uniquely identified test guests and artifacts. Preserve guest
  data if safe cleanup cannot finish, and never remove or reconfigure shared
  host networking as cleanup. Capture redacted diagnostics on failure.
- [x] Record repeatable invocation guidance and per-scenario results with
  environment limits, including bridge/NAT regression results where relevant.
  Missing host, external client, image or network prerequisites are pending
  acceptance, not passed or silently omitted cases. This ticket is not complete
  until the required real-host checks pass.
- [x] Commit harness and evidence work with relevant checks, a Conventional
  Commit, detailed body and no co-author trailer. If evidence remains pending,
  preserve that status even when the harness itself is committed. Suggested
  subject: test: verify macvtap lifecycle on KVM.
