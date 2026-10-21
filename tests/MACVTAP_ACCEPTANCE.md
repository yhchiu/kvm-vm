# Macvtap acceptance from a separate network client

Run `macvtap_acceptance.py` on a computer that can reach both the KVM host's SSH
service and the guest LAN addresses. This workflow executes real CLI operations;
it is opt-in and is not part of the fast smoke suite. The controller needs Python,
PyYAML, OpenSSH and an existing authorized SSH key. The KVM host needs the normal
create/clone/reinstall tools and a supported cloud image.

Reserve two distinct guest IPv4 addresses before starting. Stop any existing
guest using either address. Confirm the upstream switch/provider allows multiple
MAC addresses behind the physical wired parent. The harness never modifies host
addresses, bridges, routes, firewall rules or interface configuration.

```bash
python3 tests/macvtap_acceptance.py \
  --host root@kvm-host --host-port 22 --key ~/.ssh/id_ed25519 \
  --remote-cli /path/to/checkout/kvm-vm --parent eno1 \
  --address 192.0.2.10/24 --peer-address 192.0.2.11/24 \
  --gateway 192.0.2.1 --dns 192.0.2.53 \
  --report macvtap-static.json --execute-with-reserved-addresses
```

Use real reserved addresses, not the documentation example. The remote checkout
must contain the implementation under test. `--distro` defaults to `debian13`.
For DHCP coverage, add `--dhcp --source-mac 52:54:00:12:34:56` using the actual
reserved MAC and provide its expected DHCP-reserved source address; the peer
remains static. DHCP availability and reservation policy are
external prerequisites. A static run does not establish DHCP support.

If provider DHCP is unavailable or unknown, Debian/Ubuntu runs can use
`--controlled-dhcp`. After static lifecycle checks, the peer guest installs
`dnsmasq-base` and serves only the source guest's exact MAC and single reserved IP;
all unknown clients are ignored. DNS serving is disabled. The source is recreated
with DHCP, and the test checks its lease and external SSH login. Both guests use
the same physical parent; no host networking is changed. This proves the guest
DHCP path with a controlled server, not provider DHCP availability. Deleting the
peer removes this temporary service with its disposable disk.
The unknown-client restriction follows the upstream
[dnsmasq tag documentation](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html).

The workflow creates unique `e2e-macvtap-*` guests, validates a single direct
interface and its saved parent/model/MAC, proves external key login and guest
gateway access, clones via the environment override, checks guest-to-guest
traffic, and exercises reinstall with conflicting network overrides, reboot and
deletion. Guest-agent address discovery is reported independently; a missing
agent response is not treated as a failed external connection. No external DHCP
lease is expected in libvirt's own lease database.

The host isolation probe binds ICMP to the parent interface to exclude a routed
bridge or other alternate path. It runs only after successful external and peer
connectivity, so an unreachable guest cannot masquerade as correct isolation.

Success deletes the run's guests through the real CLI. Failure retains the named
guests and artifacts for diagnosis. Inspect the report, reconcile domain/storage
state, and delete only its exact run-owned names with `kvm-vm delete` when safe.
The remote `/tmp/e2e-macvtap-*` YAML inputs and report-specific known-hosts file
are nonsecret evidence and may be removed explicitly after diagnosis. Do not
delete shared bridges or change the physical NIC as cleanup. Restore any existing
guests stopped during preparation only after their IPs are no longer in use.

The JSON report records host versions, parent state, scenario results and failure
diagnostics. Console authentication is disabled here and is a separate matrix.
Missing DHCP, images or client reachability remain pending acceptance, not passes.
