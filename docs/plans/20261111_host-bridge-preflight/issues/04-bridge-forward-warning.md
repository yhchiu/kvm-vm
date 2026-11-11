# 04: Warn when the host firewall drops bridged guest IPv4

**Status:** complete

**Bug:** B-005, in kvm-vm. `create` / `validate` succeed, the guest has an
address, and the host can reach the guest, while guest IPv4 through the
bridge is dropped. Seen on a Debian 13 Docker host: `br_netfilter` loaded,
`net.bridge.bridge-nf-call-iptables=1`, iptables FORWARD policy DROP, and
libvirt ACCEPT rules only for `virbr0`.

**Decision:** Warn, do not refuse, and do not change firewall rules.
`validate` stays offline (it still does not catch a missing bridge). The
warning runs from `validate_bridge`, which `create`, `clone` and `reinstall`
already call, including `--dry-run`. Document how to allow FORWARD for the
bridge without setting the policy to ACCEPT, and the per-bridge
`nf_call_iptables=0` alternative. The tool's own warning is issue 01.

**Tests:** Parser and warning unit tests. `validate_bridge` logs a warning
and still returns when the bridge exists and the firewall would drop it.

- [x] Detect br_netfilter, the sysctl and the FORWARD chain
- [x] Warn from bridge validation used by create, clone and reinstall
- [x] Document the warning and the allow rules in both languages
- [x] Unit tests
