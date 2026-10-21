# Ticket 05 validation

Fixed review point: `0a65249`. Status: acceptance passed; review pending.

## Environment

Authorized Debian 13 KVM host, Linux 6.12, libvirt 11.3.0 and QEMU 10.0.13.
Exact kernel: `6.12.101+deb13-amd64`; virt-install: `5.0.0`;
OpenSSL: `3.5.6`. The Debian 13 base image SHA-256 is
`07f9fe05fe7cb1702047034830150baddd66c8be06e233bb226c84aa8a275263`.
Standalone wired parent `enp4s0` has `216.244.76.242/29`; gateway is
`216.244.76.241`. The independent Windows controller connects through the routed
network directly to the guest addresses. Same-parent guest-to-guest traffic is
tested separately, rather than claiming that Windows shares the broadcast LAN.

The operator supplied `.243` through `.246` for testing. Existing `vh-mon` and
`vh-pb` were gracefully stopped to release `.246` and `.245`; their disks and
definitions were retained. Other guests were not changed. The parent addresses,
master, link state and MTU are captured before/after; no physical NIC or shared
network configuration is changed by the harness.

Debian 13 uses the host's cached generic cloud image. DHCP availability from the
provider is unknown. Controlled DHCP coverage uses a disposable peer guest,
responds only to the test source's exact MAC and reserved address, ignores all
unknown clients and does not run a DNS service. This does not assert that the
provider supplies DHCP.

## Findings and verification

- Initial static run `24cba2a9` failed because the harness allowed only 30 seconds
  for cloud-init. External SSH subsequently worked, cloud-init reported done,
  and guest-agent service was active. The harness now uses its explicit
  provisioning timeout. The failed run's guest was removed through the CLI.
- Static run `b96201e5` proved external key login, guest gateway access,
  environment-selected clone attachment and bidirectional guest-to-guest ping.
- That clone exposed missing agent hardware with `generic` OS defaults.
  Commit `10a299f` explicitly declares the enabled agent channel. New create and
  clone regression tests failed before the fix; all 278 fast tests and Linux
  smoke pass afterward.
- Static run `b96201e5` completed all network lifecycle checks, host-parent isolation, source agent discovery, deletion and unchanged-parent checks. The clone channel finding was repaired before the final complete run.
- Final run `15d6def2` passed every scenario with the channel fix: explicit and
  override-selected direct NICs, external key login, gateway and peer traffic,
  parent-bound host isolation, actual agent discovery, saved-network reinstall,
  reboot confirmed by changed boot ID, controlled DHCP lease plus external key
  login, deletion and unchanged parent state.
- Both source and peer XML contain the enabled agent channel. The controlled
  DHCP server served only the source MAC and reserved `.246` address; upstream
  provider DHCP remains unknown and is not claimed as verified.
- All run-owned guests were deleted by the real CLI. Original `vh-mon` and
  `vh-pb` were restarted afterward. Their definitions/disks were never deleted.
- Final smoke passed with 278 fast tests and harness compilation. Existing
  bridge/NAT behavior is covered by that regression suite; this ticket's real
  network run covers Macvtap, not a new full real-image bridge/NAT matrix.

The complete nonsecret final report is retained as
[ticket-05-macvtap-results.json](ticket-05-macvtap-results.json).

Repeatable invocation and prerequisite handling are documented in
[the acceptance guide](../../../tests/MACVTAP_ACCEPTANCE.md).
