# 01: Add kvm-vm-bridge

**Status:** complete

**What to build:** An optional tool that creates one Linux bridge on an
uplink, moves the host address and default route, arms an auto-rollback
timer before any change, and writes persistent ifupdown, NetworkManager or
systemd-networkd configuration only at confirm time.

**Also included:** After a successful auto-rollback, archive the session so
the next create is not blocked (B-004). On create and confirm, warn when
`br_netfilter` plus `bridge-nf-call-iptables=1` plus a FORWARD DROP/REJECT
with no ACCEPT for the bridge would drop guest IPv4 (B-005 for the tool).

**Tests:** `tests/test_bridge_tool.py`, offline. Live dry-run skipped unless
`KVM_VM_BRIDGE_LIVE=1`.

- [x] Tool at `tools/kvm-vm-bridge`, installed by `install.sh`
- [x] Watchdog uses valid `systemd-run` flags (`--no-block`, no
  `--remain-after-exit=no`) and `setsid -f` for the nohup fallback
- [x] Auto-rollback archives the session
- [x] create and confirm warn about FORWARD drops
- [x] Offline tests
- [x] English and Traditional Chinese docs, linked from the networking docs
