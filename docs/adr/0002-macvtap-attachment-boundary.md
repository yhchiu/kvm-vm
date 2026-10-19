---
status: accepted
date: 2026-10-19
---

# Attach Macvtap VMs to the LAN without a second management NIC

VMs need their own LAN addresses without moving the host's physical interface
or IP configuration into a Linux bridge. We will support one Macvtap NIC on an
existing wired Ethernet parent, with the Macvtap forwarding mode fixed to
bridge. The operator owns host network configuration; kvm-vm validates and
uses it without creating interfaces or changing host addresses.

## Decision and alternatives

Keep a single NIC. Use another LAN computer for network management, and serial
console for host-local management, with optional password authentication as
defined in [ADR 0003](0003-explicit-console-password-authentication.md).
Macvtap alone does not provide host-to-guest communication through its parent.
Console access is a separate path, not a promise that an unhealthy guest can
boot or authenticate.

A Linux bridge remains an existing alternative but requires the host networking
setup this use case seeks to avoid. A second Isolated NIC would enable host SSH;
a second NAT NIC would additionally provide an outbound forwarding path. Both
would introduce another address, interface, route policy and shared network
lifecycle. We accept the Macvtap host-path limitation instead of adding those
responsibilities. Bond, VLAN, Wi-Fi, other Macvtap forwarding modes and general
multi-NIC configuration are outside the initial support boundary.

The configuration interface selects Macvtap explicitly and identifies its
parent. A single environment override selects both mode and parent. Conflicting
bridge and Macvtap overrides fail explicitly. Persist the effective topology;
reinstall retains that topology rather than applying ambient network overrides.
Existing bridge/NAT behavior remains unchanged outside these new conflicts.

## Consequences

Host network setup stays under operator control and VM configuration remains
single-NIC. Host-originated SSH cannot serve as the Macvtap acceptance test:
LAN connectivity must be tested from another LAN system. Console acceptance
must separately prove a usable login path. Authentication is configured
explicitly rather than changed automatically by the network mode.

This records an accepted design, not implemented or verified support. Detailed
configuration, upstream evidence and acceptance checks are maintained in the
[evaluation](../macvtap-evaluation.md). Libvirt documents the host-path
limitation and second-NIC alternative in its
[Macvtap troubleshooting guide](https://wiki.libvirt.org/TroubleshootMacvtapHostFail.html).
