# 05: Do not treat per-bridge nf_call_iptables=0 as off

**Status:** complete

**Bug:** B-006. After B-005, `kvm-vm create --dry-run` on an existing bridge
printed no firewall warning. `kvm-vm-bridge confirm` uses the same check.
Host A had `net.bridge.bridge-nf-call-iptables=1` and
`/sys/class/net/br0/bridge/nf_call_iptables=0`.

**Kernel:** `br_nf_pre_routing` returns `NF_ACCEPT` only when both
`brnet->call_iptables` and `BROPT_NF_CALL_IPTABLES` are clear. Either knob
being 1 sends bridged IPv4 to iptables. A per-bridge 0 does not disable a
global 1. The same OR applies to IPv6.

**Decision:** The check returns "1" when either value is 1. It returns "0"
only when the global sysctl was read and neither value is 1. Docs must not
offer per-bridge `nf_call_iptables=0` as a way to exempt one bridge.

**Tests:** Unit tests for the OR, including global 1 with per-bridge 0 still
producing the FORWARD warning. Offline only.

- [x] OR the two knobs in kvm-vm and kvm-vm-bridge
- [x] Remove the per-bridge 0 recipe from the networking docs
- [x] Unit tests
