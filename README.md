# kvm-vm

`kvm-vm` is a small declarative VM manager for a long-lived standalone KVM/libvirt host.
It uses standard tools (`virsh`, `virt-install`, `qemu-img`, cloud-init) and stores an
effective YAML definition plus a small state manifest for every VM it creates.

The design deliberately stays below the complexity of OpenStack/Proxmox while making
repeatable VM provisioning much safer than ad-hoc `virt-install` command lines.

## Commands

```text
kvm-vm create <vm.yaml> [--refresh-image] [--no-start] [--dry-run]
kvm-vm clone <source-vm> <target.yaml> [--no-start] [--dry-run]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
kvm-vm validate <vm.yaml> [--clone]
```

## Host dependencies

Debian / Ubuntu (package names may vary slightly by release):

```bash
apt install \
  qemu-system-x86 qemu-utils \
  libvirt-daemon-system libvirt-clients \
  virtinst cloud-image-utils \
  python3 python3-yaml \
  libguestfs-tools libosinfo-bin iproute2
```

RHEL / Rocky / Alma family:

```bash
dnf install \
  qemu-kvm libvirt virt-install \
  cloud-utils python3-pyyaml \
  libguestfs-tools libosinfo iproute
```

`virt-sysprep` and `virt-customize` are needed only for `clone`, but installing them on
a management host is recommended.

## Install

```bash
sudo ./install.sh
```

The default libvirt URI is `qemu:///system`.

## Directory layout

```text
/etc/kvm-vm/definitions/             effective YAML definitions
/var/lib/kvm-vm/state/               tool state manifests
/var/lib/libvirt/images/base/        cached distribution cloud images
/var/lib/libvirt/images/vm/          independent VM qcow2 system disks
/var/lib/libvirt/images/cloud-init/  per-VM NoCloud seed and source files
```

Cloud-init media is kept below `/var/lib/libvirt/images` so it naturally fits common
libvirt/QEMU ownership and SELinux policies. `restorecon` is used on SELinux hosts when
available.

All locations can be overridden with environment variables:

```text
KVM_VM_LIBVIRT_URI
KVM_VM_BASE_DIR
KVM_VM_DISK_DIR
KVM_VM_STATE_DIR
KVM_VM_CLOUD_DIR
KVM_VM_DEF_DIR
```

## Create a VM

Start with `examples/ubuntu24-web01.yaml` and edit the network and SSH key path. The default network mode is `bridge`, and the default bridge is `viifbr0`.

```bash
kvm-vm validate web01.yaml
sudo kvm-vm create web01.yaml
```

`create` currently knows these convenience distro aliases:

```text
ubuntu24.04
debian13
rocky9
almalinux9
```

You may instead provide your own image:

```yaml
storage:
  disk_gib: 80
  image:
    path: /srv/images/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

or:

```yaml
storage:
  disk_gib: 80
  image:
    url: https://images.example.com/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

For reproducible production provisioning, a pinned URL/local image plus `sha256` is
preferable to a moving `current`/`latest` distro alias.

Each VM receives a **full independent qcow2 disk**. Existing VMs do not depend on the
cached base image, so refreshing or deleting a base image cannot break them.

## YAML definition

Example:

```yaml
version: 1

vm:
  name: web01
  vcpus: 4
  memory_mib: 8192
  cpu: host-passthrough
  autostart: true
  start: true
  os_variant: ubuntu24.04

storage:
  disk_gib: 80
  bus: virtio
  cache: none
  discard: unmap
  image:
    distro: ubuntu24.04

network:
  mode: bridge
  bridge: viifbr0
  model: virtio
  mac: auto
  ipv4:
    method: static
    address: 192.168.10.51/24
    gateway: 192.168.10.1
    dns:
      - 192.168.10.1
      - 1.1.1.1
  ipv6:
    method: disabled

cloud_init:
  user: admin
  ssh_authorized_keys:
    - file:/root/.ssh/id_ed25519.pub
  package_update: false
  qemu_guest_agent: true
  timezone: Asia/Taipei
  packages:
    - curl
    - vim
  runcmd: []
```

SSH keys can be literal OpenSSH public keys or `file:/path/to/key.pub`. Relative `file:`
paths are resolved relative to the YAML file.

`mac: auto` is replaced by an actual `52:54:00:*` address in the stored effective YAML.
Static cloud-init networking matches that MAC and renames the interface to `eth0`, so
the definition does not depend on whether a distribution originally calls it `ens3`,
`enp1s0`, etc.

## Network modes

`kvm-vm` supports two single-NIC attachment modes.

### Bridge mode

Bridge mode is the default. If `network.mode` and `network.bridge` are omitted, the VM is
attached to `viifbr0`:

```yaml
network:
  mode: bridge
  bridge: viifbr0
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
```

This produces the equivalent of:

```text
virt-install ... --network bridge=viifbr0,model=virtio,mac=...
```

For backward compatibility, existing YAML that contains only `network.bridge` still uses
bridge mode. The default bridge changed from `br0` in v1.0.0 to `viifbr0` in v1.1.0.

### NAT mode

NAT mode attaches the VM to a libvirt virtual network, not directly to that virtual
network's Linux bridge. The common libvirt `default` network is typically backed by
`virbr0`, dnsmasq/DHCP, and outbound NAT.

```yaml
network:
  mode: nat
  libvirt_network: default
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
  ipv6:
    method: disabled
```

This produces the equivalent of:

```text
virt-install ... --network network=default,model=virtio,mac=...
```

Before `create` or `clone`, `kvm-vm` verifies that the named libvirt network exists, is
active, and has NAT forwarding configured. If it exists but is inactive, start it with:

```bash
virsh -c qemu:///system net-start default
virsh -c qemu:///system net-autostart default
```

`examples/ubuntu24-nat.yaml` is a complete NAT example. DHCP is normally the simplest
choice for a NAT network because libvirt's network DHCP service then owns address
allocation. A static guest address is still accepted, but you are responsible for keeping
it inside the NAT subnet and outside conflicting DHCP allocations/reservations.

## List and status

Fast list without IP discovery:

```bash
kvm-vm list
```

Only tool-managed VMs:

```bash
kvm-vm list --managed
```

Include IP discovery (slower because it probes guest-agent, DHCP lease, and ARP data):

```bash
kvm-vm list --ips
```

Detailed view:

```bash
kvm-vm status web01
```

`status` shows libvirt domain information, the kvm-vm state manifest, disks, interfaces,
and addresses known to libvirt.

## Serial console

```bash
kvm-vm console web01
```

Exit `virsh console` with `Ctrl+]`.

## Safe delete behavior

```bash
kvm-vm delete web01
```

The command asks you to type the VM name. For automation:

```bash
kvm-vm delete web01 --yes
```

A running VM is **not** destroyed implicitly. Shut it down first, or explicitly request:

```bash
kvm-vm delete web01 --force --yes
```

Important safety rule: `kvm-vm` does **not** use `virsh undefine --remove-all-storage`.
It deletes only the system disk and cloud-init media recorded in its own state manifest.
Therefore a data disk attached later by an administrator is not accidentally deleted.

For an unmanaged pre-existing VM, storage deletion is refused. You may undefine it while
leaving every disk untouched:

```bash
kvm-vm delete legacy01 --keep-storage
```

For a managed VM you can also keep all managed files:

```bash
kvm-vm delete web01 --keep-storage
```

The state/definition is retained in this case to avoid silently orphaning storage.

## Clone

A clone target YAML looks like `examples/clone-target.yaml`. It intentionally does not
need `storage.image`, because the source VM disk is the source image.

```bash
virsh shutdown web01
# Wait for: virsh domstate web01 -> "shut off"
kvm-vm validate --clone web02.yaml
sudo kvm-vm clone web01 web02.yaml
```

Clone is deliberately **offline only**. The source domain must be shut off.

The workflow is:

```text
source system disk
      |
      +-- qemu-img convert --> independent target qcow2
                                  |
                                  +-- virt-sysprep
                                  |     machine-id
                                  |     SSH host keys
                                  |     hostname/network stale state
                                  |     DHCP state
                                  |     random seed
                                  |
                                  +-- cloud-init clean
                                  |
                                  +-- new MAC + new NoCloud instance-id
                                  |
                                  +-- target YAML network/user settings
```

Only safe, explicitly selected `virt-sysprep` operations are used. The script does **not**
run the broad default operation set, because a clone should not unexpectedly remove user
accounts or unrelated application state.

Current clone limitations are intentional:

- Linux guests only (`virt-sysprep` limitation).
- Source VM must be shut off.
- The system disk must currently be a regular file-backed disk.
- Only the system disk is cloned; separately attached data disks are not cloned.
- A smaller target virtual disk is refused. A larger target is allowed; cloud images or
  guest tooling are still responsible for expanding partitions/filesystems as appropriate.

## CPU model note

The examples use:

```yaml
cpu: host-passthrough
```

This maximizes access to the host CPU features and is appropriate for a stable single-host
or homogeneous-host setup. If live migration between different CPU generations is a goal,
use a deliberately selected migratable CPU model instead.

## Base image refresh

Distro aliases are cached. Existing VMs are independent and are not changed.

```bash
kvm-vm create newvm.yaml --refresh-image
```

## `--no-start`

Both `create` and `clone` support:

```bash
kvm-vm create vm.yaml --no-start
```

The domain is defined but left shut off, which is useful if you want to inspect `virsh
dumpxml` or apply additional libvirt policy before first boot.

## `--dry-run`

Both `create` and `clone` support:

```bash
kvm-vm create vm.yaml --dry-run
kvm-vm clone source-vm target.yaml --dry-run
```

Runs all pre-flight checks (YAML normalization, domain/state collision checks, bridge/NAT
validation, MAC allocation, and SSH key resolution) and prints the normalized YAML without
downloading images, touching disks, or defining libvirt domains.

## Version notes

### 1.1.0

- Changed the default bridge from `br0` to `viifbr0`.
- Added `network.mode: nat` with `libvirt_network: default`.
- NAT definitions are validated against the active libvirt virtual network and its forward mode.
- Existing bridge-mode YAML remains compatible.

## Recommended operational workflow

Keep your authored YAML files in Git, for example:

```text
infra/
  kvm/
    web01.yaml
    web02.yaml
    db01.yaml
```

Then use:

```bash
kvm-vm validate infra/kvm/web01.yaml
kvm-vm create infra/kvm/web01.yaml
kvm-vm status web01
```

Treat `/etc/kvm-vm/definitions/*.yaml` as the **effective deployment record** generated by
the tool, not as the primary source-of-truth. This separates reviewed desired definitions
from the exact settings (including generated MAC address) used on the host.

## What this tool deliberately does not do

It is not a replacement for a cluster manager. It does not currently manage:

- live migration
- Ceph/RBD storage
- snapshots/backups
- multiple NICs
- multiple managed data disks
- VLAN tagging
- Windows sysprep
- reconciliation/drift correction of an already-created VM

Those can be added later without changing the basic YAML/state model.
