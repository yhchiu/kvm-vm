English | [繁體中文](networking.zh-TW.md)

# Networking

Each VM has one network card, attached in one of three modes. Different VMs on
the same host can use different modes.

- [Choose a mode](#choose-a-mode)
- [Bridge mode](#bridge-mode)
- [NAT mode](#nat-mode)
- [Macvtap mode](#macvtap-mode)
- [IP addresses](#ip-addresses)
- [Find a VM's address](#find-a-vms-address)

## Choose a mode

| | Bridge | NAT | Macvtap |
|---|---|---|---|
| Other machines on the LAN can reach the VM | Yes | No (outbound only) | Yes |
| The KVM host can reach the VM over the network | Yes | Yes | **No** |
| What the host needs first | A Linux bridge, such as `br0` | A libvirt NAT network, usually `default` | A free physical Ethernet interface |
| Good for | Servers on the LAN | Labs, tests, a first try | LAN servers when the host's interface cannot become a bridge |

kvm-vm never creates, changes or deletes host bridges, libvirt networks or
physical interfaces. Set them up first with your distribution's tools.

## Bridge mode

Bridge mode is the default. When `network.mode` and `network.bridge` are left
out, the VM is attached to `br0`.

```yaml
network:
  mode: bridge
  bridge: br0
  ipv4:
    method: dhcp
```

This is the same as `virt-install --network bridge=br0,model=virtio,mac=...`.

`KVM_VM_BRIDGE=name` selects bridge mode and the bridge at run time. A definition
that sets only `network.bridge`, without `mode`, also uses bridge mode.

## NAT mode

NAT mode attaches the VM to a libvirt virtual network. libvirt's usual `default`
network uses the `virbr0` bridge, gives out addresses with DHCP, and NATs
outbound traffic. The VM can reach the internet; other machines cannot reach the
VM.

```yaml
network:
  mode: nat
  libvirt_network: default
  ipv4:
    method: dhcp
```

This is the same as `virt-install --network network=default,model=virtio,mac=...`.
[`examples/ubuntu24-nat.yaml`](../examples/ubuntu24-nat.yaml) is a complete
example.

Before `create` or `clone`, kvm-vm checks that the network exists, is active and
uses NAT forwarding. If it exists but is not active:

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

Use DHCP with NAT: libvirt then hands out the addresses. A static address also
works, but you must keep it inside the NAT subnet and away from the DHCP range
and any reservations.

## Macvtap mode

Macvtap puts the VM directly on the physical LAN through one of the host's
Ethernet interfaces (the **parent**), without turning that interface into a
Linux bridge.

```yaml
network:
  mode: macvtap
  parent: eno1
  ipv4:
    method: dhcp
```

Or switch an existing definition at run time:

```bash
sudo KVM_VM_MACVTAP=eno1 KVM_VM_IPV4=dhcp \
  kvm-vm create examples/ubuntu24-web01.yaml
```

Replace `eno1` with your interface. For a static address, use `ipv4.address`,
`gateway` and `dns`, or `KVM_VM_IPV4`, `KVM_VM_GATEWAY` and `KVM_VM_DNS`.
`KVM_VM_MACVTAP` also switches bridge and NAT definitions to Macvtap. Do not set
`KVM_VM_BRIDGE` at the same time; kvm-vm refuses the combination.

### Macvtap limitations

- **The host cannot reach the VM over the network.** This covers SSH, ping and
  every other IP service, even on the same subnet. Connect from another machine
  on the LAN. `list --ips` showing an address does not mean the host can reach it.
- **Manage the VM from the host with the serial console.** Run
  `sudo kvm-vm console NAME`. To log in there with a password, turn on
  [console password login](access.md#console-password-login) before you create
  the VM; passwords are locked by default.
- **One network card per VM.** kvm-vm does not add a second NAT or management
  card. Whether VMs in different modes can talk to each other depends on your
  routing and firewalls.

### Parent interface requirements

- The parent must exist, be up, and be a standalone Ethernet interface. kvm-vm
  checks it through `/sys/class/net` and `ip -j -d link`.
- It cannot be a member of a Linux bridge or bond. If `eno1` is already in
  `br0`, use another interface for Macvtap.
- Wi-Fi, bonds, VLAN interfaces, bridges and enslaved interfaces are not
  supported.
- Macvtap always uses its `bridge` forwarding mode, so VMs on the same parent can
  reach each other.

`validate` checks the definition only. `create --dry-run` also inspects the
parent on the host.

### LAN requirements

The LAN must accept extra MAC addresses on the host's switch port and give the VM
an address (DHCP or a static address you assign). kvm-vm cannot check this.
Switch or provider limits on MAC addresses can block the VM even when every local
check passes. Ask your network administrator.

### Saved settings

`create` and `clone` save the network attachment in the effective definition.
`reinstall` keeps it and ignores `KVM_VM_MACVTAP` and other overrides. When you
restore a definition on another host, make sure a suitable parent exists there.

Static addresses, LAN DHCP, guest agent address discovery and the VM lifecycle
passed [real-host acceptance](plans/20261019_macvtap-console/ticket-05-validation.md).
Your LAN must still meet the requirements above.

## IP addresses

| `ipv4.method` | Meaning |
|---|---|
| `dhcp` (default) | Get an address from DHCP. |
| `static` | Use `address` (with prefix, such as `192.168.10.51/24`), `gateway` (required) and `dns` (optional list). |
| `disabled` | No IPv4. |

IPv6 is `disabled` by default. See the [field reference](configuration.md#network)
for the other options.

## Find a VM's address

```bash
sudo kvm-vm list --ips
sudo kvm-vm status web01
```

kvm-vm asks libvirt for addresses from three sources: the QEMU guest agent, the
libvirt DHCP leases, and the host's ARP table. This is slower than a plain `list`.

- NAT VMs appear in libvirt's DHCP leases.
- Bridge and Macvtap VMs usually get their address from a DHCP server that
  libvirt does not know about. Their address then comes from the guest agent,
  which needs `cloud_init.qemu_guest_agent: true` (the default).
- The guest agent is installed at first boot. Wait until cloud-init has finished
  before relying on `list --ips`.
