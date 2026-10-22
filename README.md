English | [繁體中文](README.zh-TW.md)

# kvm-vm

`kvm-vm` is a small declarative VM manager for a long-lived standalone KVM/libvirt host.
It uses standard tools (`virsh`, `virt-install`, `qemu-img`, cloud-init) and stores an
effective YAML definition plus a small state manifest for every VM it creates.

The design deliberately stays below the complexity of OpenStack/Proxmox while making
repeatable VM provisioning much safer than ad-hoc `virt-install` command lines.

## Commands

```text
kvm-vm create <vm.yaml> [--console-password-file <path>] [--refresh-image] [--no-start] [--dry-run]
kvm-vm clone <source-vm> <target.yaml> [--console-password-file <path>] [--no-start] [--dry-run]
kvm-vm reinstall <vm> [--console-password-file <path>] [--os <os>] [--image-url <url>] [--image-path <path>] [--refresh-image] [--no-start] [--dry-run] [--yes] [--force]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
kvm-vm snapshot create <vm> [--no-quiesce] [--dry-run]
kvm-vm snapshot status <vm>
kvm-vm snapshot delete <vm> [--yes]
kvm-vm snapshot rollback <vm> [--yes] [--force] [--no-start]
kvm-vm backup check <backup-dir> [--until <checkpoint>]
kvm-vm validate <vm.yaml> [--clone]
```

## Host dependencies

### Python requirements
- **Python 3.8+**
- **Runtime dependencies**: `PyYAML` (`python3-yaml` on Debian/Ubuntu, `python3-pyyaml` on RHEL-family, or `pip install pyyaml`). All other modules use the Python standard library.
- **No C bindings required**: `kvm-vm` intentionally uses standard CLI tools (`virsh`, `virt-install`, `qemu-img`) via subprocess and does **not** require `libvirt-python` C bindings, avoiding compilation and ABI mismatch issues.
- **Testing (optional)**: `pytest` (`python3-pytest` or `pip install pytest`) for running the test suite.

### System packages

Debian / Ubuntu (package names may vary slightly by release):

```bash
apt install \
  qemu-system-x86 qemu-utils \
  libvirt-daemon-system libvirt-clients \
  virt-install cloud-image-utils \
  python3 python3-yaml \
  guestfs-tools libosinfo-bin iproute2 openssh-client openssl
```

*(Note: On Debian 11 or older releases, use `virtinst` instead of `virt-install`, and `libguestfs-tools` instead of `guestfs-tools`)*

RHEL / Rocky / Alma family:

```bash
dnf install \
  qemu-kvm qemu-img libvirt virt-install \
  xorriso python3 python3-pyyaml \
  guestfs-tools libosinfo iproute openssh-clients openssl

# Start and enable libvirt daemon (RHEL does not start services automatically)
sudo systemctl enable --now libvirtd
```

*(Note: On RHEL 8 / Rocky 8 or older releases, use `genisoimage` instead of `xorriso`, and `libguestfs-tools` instead of `guestfs-tools`)*

For building cloud-init seed images, `kvm-vm` automatically detects and uses any available tool: `cloud-localds` (from `cloud-image-utils` / `cloud-utils`), `genisoimage`, `mkisofs`, or `xorriso`.

`virt-sysprep` and `virt-customize` are needed only for `clone`, but installing them on
a management host is recommended.

OpenSSL is required when enabling console passwords. Credential delivery requires
POSIX ownership and permissions on the Linux management host.

### SSH key

The example VM definitions (`examples/*.yaml`) configure default cloud-init access using `~/.ssh/id_ed25519.pub`. Ensure this public key exists on your host before creating VMs with the provided examples.

If you do not have an Ed25519 key pair, generate one with:

```bash
ssh-keygen -t ed25519
```

Press `Enter` to accept the default file path (`~/.ssh/id_ed25519`).

## Install

```bash
sudo ./install.sh
```

The default libvirt URI is `qemu:///system`.

### Upgrade

```bash
sudo ./install.sh
```

The install script is idempotent; re-running it upgrades `kvm-vm` in place.

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

All storage paths can be overridden with environment variables; see [Environment variables](#environment-variables).

## Create a VM

Start with `examples/ubuntu24-web01.yaml` and edit the network and SSH key path. The default network mode is `bridge`, and the default bridge is `br0` (customizable via `KVM_VM_BRIDGE`).

```bash
kvm-vm validate web01.yaml
sudo kvm-vm create web01.yaml
```

Environment variables can be combined with a YAML file to override specific values at deploy time (see [Environment variables](#environment-variables)):

```bash
sudo KVM_VM_OS=debian13 KVM_VM_NAME=web02 KVM_VM_VCPUS=16 KVM_VM_DISK=100 \
  KVM_VM_MEMORY=32000 KVM_VM_IPV4=192.168.10.52/24 \
  kvm-vm create examples/ubuntu24-web01.yaml
```

This takes `examples/ubuntu24-web01.yaml` as the base definition and overrides the OS, name, resources, and IP address via environment variables, creating a completely different VM (`web02`) without writing a new YAML file.

### Distro aliases

`create` currently knows these convenience distro aliases:

```text
ubuntu24.04
ubuntu26.04
debian12
debian13
rocky8
rocky9
rocky10
almalinux8
almalinux9
almalinux10
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

### Connect to a VM

After the VM is created and cloud-init completes its initial provisioning:

> [!IMPORTANT]
> **Macvtap access**: Run SSH from another computer that can reach the VM's LAN
> address. The KVM host cannot connect directly to its Macvtap guest through the
> parent interface, even when both are on the same subnet. For management from
> the host, use `sudo kvm-vm console web01` and provision
> [console password login](#optional-console-password-login) beforehand; passwords
> are locked by default. See [Macvtap limitations](#macvtap-limitations).

1. **Find the assigned IP address**:

```bash
kvm-vm list --ips
# or detailed status:
kvm-vm status web01
```

2. **Connect via SSH**:

Connect using the username defined in `cloud_init.user` (default in examples: `admin`) and your matching private key:

```bash
ssh admin@<vm-ip> -i ~/.ssh/id_ed25519
```

If `cloud_init.ssh_port` is set to a custom port, add it to the connection command:

```bash
ssh -p 2222 admin@<vm-ip> -i ~/.ssh/id_ed25519
```

> [!NOTE]
> **SSH key-only security policy**:
> SSH remains key-only. The management user's password is locked by default;
> [optional console password login](#optional-console-password-login) enables a local
> password without enabling SSH password authentication or setting a root password.
> See [Security policy and out-of-band recovery](#security-policy-and-out-of-band-recovery) for emergency access.

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
  bridge: br0
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
  console_password: false
  ssh_port: 22
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
  package_update: false
  qemu_guest_agent: true
  timezone: Asia/Taipei
  packages:
    - curl
    - vim
  runcmd: []
```

`cloud_init.ssh_port` accepts an integer from `1` to `65535` and defaults to `22`.
With the default, the tool leaves the image's SSH port configuration unchanged.
For example, set `ssh_port: 2222` to listen on TCP port 2222. During first-boot
provisioning, after your `runcmd` commands, the tool replaces active `Port` directives
in `/etc/ssh/sshd_config` and `/etc/ssh/sshd_config.d/*.conf`, validates the SSH
configuration, and restarts the listener. Where `ssh.socket` starts SSH, as on Ubuntu,
the tool also writes `/etc/systemd/system/ssh.socket.d/zz-kvm-vm-port.conf`. That file
sets the socket's port, so edit or remove it if you later change the port by hand.
Clones use the target definition's port (or 22 when omitted). A clone that uses 22
gets the source guest's `Port` directives and socket file removed, so SSH returns to
the image default. Reinstalls preserve the saved port. Editing a definition alone does
not update a running VM.

Allow the chosen port through any guest or upstream firewall. On SELinux-enabled
guests such as Rocky/Alma, authorize a custom port before SSH restarts; for example,
for a port not already assigned in SELinux policy:

```yaml
cloud_init:
  ssh_port: 2222
  packages:
    - policycoreutils-python-utils
  runcmd:
    - [semanage, port, -a, -t, ssh_port_t, -p, tcp, '2222']
```

Merge these fields into your existing `cloud_init` section, keeping the user and SSH keys.

If SSH cannot start on the new port, for example because SELinux or another service
blocks it, the tool restores the previous SSH files and restarts SSH. The guest stays
reachable on the port it used before (22 on a new VM), cloud-init reports an error,
and `/var/log/cloud-init-output.log` shows `kvm-vm: could not move SSH to port <port>`.

SSH keys can be literal OpenSSH public keys or `file:/path/to/key.pub`. Relative `file:`
paths are resolved relative to the YAML file. Tilde expansion (`file:~/.ssh/...`) is
supported, and when running under `sudo`, it automatically resolves to `$SUDO_USER`'s
home directory.

`mac: auto` is replaced by an actual `52:54:00:*` address in the stored effective YAML.
Static cloud-init networking matches that MAC and renames the interface to `eth0`, so
the definition does not depend on whether a distribution originally calls it `ens3`,
`enp1s0`, etc.

## Environment variables

The variables below override paths, the libvirt URI and selected definition values.
Definition overrides take precedence over YAML for `validate`, `create` and `clone`.
`reinstall` preserves saved definition values and ignores these ambient definition
overrides. Console-password opt-in is a YAML setting; credential delivery uses an
explicit CLI argument, not an environment variable.

### Paths & System Configuration

| Variable | Default | Description |
|---|---|---|
| `KVM_VM_LIBVIRT_URI` | `qemu:///system` | libvirt connection URI |
| `KVM_VM_BASE_DIR` | `/var/lib/libvirt/images/base` | Cached distribution cloud images |
| `KVM_VM_DISK_DIR` | `/var/lib/libvirt/images/vm` | Independent VM qcow2 system disks |
| `KVM_VM_STATE_DIR` | `/var/lib/kvm-vm/state` | Tool state manifests |
| `KVM_VM_CLOUD_DIR` | `/var/lib/libvirt/images/cloud-init` | Per-VM NoCloud seed and source files |
| `KVM_VM_DEF_DIR` | `/etc/kvm-vm/definitions` | Effective YAML definitions |

### VM Definition Overrides

| Variable | YAML Path | Example | Description |
|---|---|---|---|
| `KVM_VM_NAME` | `vm.name` | `web02` | Overrides VM name |
| `KVM_VM_VCPUS` | `vm.vcpus` | `4` | Overrides vCPUs count (1-1024) |
| `KVM_VM_MEMORY` / `KVM_VM_MEMORY_MIB` | `vm.memory_mib` | `4096` | Overrides memory in MiB (>= 256) |
| `KVM_VM_DISK` / `KVM_VM_DISK_GIB` | `storage.disk_gib` | `50` | Overrides disk size in GiB (>= 1) |
| `KVM_VM_OS` | `image.distro` / `os_variant` | `rocky9` | Overrides distro alias (see [Distro aliases](#distro-aliases)) and `os_variant` |
| `KVM_VM_BRIDGE` | `network.mode`, `network.bridge` | `br0` | Selects bridge mode and its bridge; conflicts with Macvtap |
| `KVM_VM_MACVTAP` | `network.mode`, `network.parent` | `eno1` | Selects Macvtap and its physical parent, including for bridge/NAT YAML; empty values and simultaneous `KVM_VM_BRIDGE` are errors |
| `KVM_VM_IPV4` | `network.ipv4` | `dhcp`, `192.168.1.50/24` | Sets method (`dhcp`, `disabled`) or static CIDR address |
| `KVM_VM_GATEWAY` | `network.ipv4.gateway` | `192.168.1.1` | Overrides default IPv4 gateway |
| `KVM_VM_DNS` | `network.ipv4.dns` | `1.1.1.1,8.8.8.8` | Overrides DNS servers (comma-separated) |

## Network modes

`kvm-vm` supports three single-NIC attachment modes: bridge, NAT and Macvtap.

### Bridge mode

Bridge mode is the default. If `network.mode` and `network.bridge` are omitted, the VM is
attached to `br0` (or the custom bridge defined by `KVM_VM_BRIDGE`):

```yaml
network:
  mode: bridge
  bridge: br0
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
```

This produces the equivalent of:

```text
virt-install ... --network bridge=br0,model=virtio,mac=...
```

For backward compatibility, existing YAML that contains only `network.bridge` still uses
bridge mode. The default bridge is `br0`, and can be customized host-wide using the
`KVM_VM_BRIDGE` environment variable.

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

### Macvtap mode

Macvtap puts the VM directly on the physical LAN without moving the host's
interface into a Linux bridge. Merge this network section into your VM definition:

```yaml
network:
  mode: macvtap
  parent: eno1
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
  ipv6:
    method: disabled
```

Or use an existing definition with deployment-time overrides:

```bash
sudo KVM_VM_MACVTAP=eno1 KVM_VM_IPV4=dhcp \
  kvm-vm create examples/ubuntu24-web01.yaml
```

Replace `eno1` with your existing physical Ethernet interface. For static IPv4,
use the same `address`, `gateway` and `dns` fields shown in the YAML example above,
or `KVM_VM_IPV4`, `KVM_VM_GATEWAY` and `KVM_VM_DNS`.

Macvtap forwarding is fixed to bridge mode. `KVM_VM_MACVTAP` selects both mode
and parent, including for bridge/NAT definitions. Empty values are errors.
Unset `KVM_VM_BRIDGE` when selecting Macvtap through YAML or the environment;
the two settings conflict.

#### Macvtap limitations

- **No direct host-to-guest connection through the parent.** This applies to SSH,
  ping and other IP services, even on the same subnet. The Macvtap bridge forwarding
  mode does not remove this restriction. Use an external computer with a working
  route to the guest for SSH; `list --ips` showing an address does not imply that
  the host can connect to it.
- **Host management uses Serial console.** Run `sudo kvm-vm console web01` on the
  host. For password login, set `cloud_init.console_password: true` and supply
  `--console-password-file` when provisioning. Attaching the console alone does
  not unlock the account. See [console password login](#optional-console-password-login).
- **One NIC per VM.** No NAT or isolated management NIC is added. Different VMs
  may use bridge, NAT and Macvtap on the same host, but a Macvtap parent cannot
  also be a member of a Linux bridge or bond. For example, if `eno1` belongs to
  `br0`, use another standalone physical interface for Macvtap. Connectivity
  between VMs using different modes depends on routing and firewall policy.

The parent must already be up, standalone and visible through Linux sysfs and
`ip -j -d link`. Missing/unsupported parents or failed inspection stop creation;
Wi-Fi, bonds, VLANs, bridges and enslaved interfaces are outside this initial
support. kvm-vm never configures or deletes the parent. `validate` checks the
definition offline; `create --dry-run` also inspects the host attachment.

The physical LAN must permit guest MAC addresses and provide DHCP or appropriate
static addressing. Confirm these with the network administrator; local preflight
cannot verify them. Switch or provider limits on additional MAC addresses may
prevent guest connectivity even when local validation succeeds.
External DHCP leases may not appear in libvirt; guest-agent address discovery
requires a working guest agent and channel. With `cloud_init.qemu_guest_agent: true`
(the default), kvm-vm adds the virtio agent channel and cloud-init installs the
guest agent. Wait for guest provisioning before relying on `list --ips`.

Create and clone save the effective attachment. Reinstall retains it and ignores
`KVM_VM_MACVTAP` and other ambient network overrides. On another host, provide
a suitable parent before using a restored definition. Serial console attachment
alone does not unlock the default user's password; see the console policy below.
Static networking, controlled LAN DHCP, guest-agent discovery and lifecycle behavior
have passed [real-host network acceptance](docs/plans/20261019_macvtap-console/ticket-05-validation.md).
Your LAN must still meet the MAC-address and addressing requirements above.

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

Attaching the console does not enable password login. To manage a Macvtap VM from
its host, opt into the management-user password described below before provisioning.
This also works with bridge and NAT; no second management NIC is required.

## Optional console password login

Set `cloud_init.console_password: true` to enable a random local password for
the configured non-root management user. This is independent of bridge, NAT or
Macvtap networking; the default remains locked. Keep SSH authorized keys configured.

Merge these fields into your existing `cloud_init` section:

```yaml
cloud_init:
  user: admin
  console_password: true
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
```

For a definition whose `vm.name` is `web01`:

```bash
sudo install -d -m 0700 /root/vm-credentials
sudo kvm-vm create web01.yaml --console-password-file /root/vm-credentials/web01.json
sudo kvm-vm console web01
```

The destination must be a new file inside a private directory owned by the
invoking administrator. Symlinks and existing destinations are rejected.
The JSON file has mode 0600 and contains `vm`, `user` and `password`, with a new
24-character random password. Read it locally through an
authorized administrative session and use its `user` and `password` at the login prompt. Passwords
are never printed by the CLI or saved in managed definitions/state; only a
salted hash enters restricted cloud-init artifacts. The host needs OpenSSL.
`validate` generates no password or credential file. For enabled policies,
`create`, `clone` and `reinstall` require `--console-password-file`, including
with `--dry-run`; preview validates the destination but does not create it.
Supplying the argument when the policy is disabled is an error.

The guest initially keeps the password locked. Final provisioning disables SSH
password and keyboard-interactive authentication, checks SSH configuration and
starts the serial getty before unlocking. SSH key login remains available.
Creation success does not prove provisioning or console login succeeded; check
cloud-init and test the login. See the [acceptance results below](#macvtap-and-console-acceptance).
No root password or autologin is enabled: `disable_root` controls SSH access and
does not by itself establish the local root account's password policy.

The password is not intrinsically limited to the serial device. Its holder gains
administrator access through the management user's existing passwordless sudo.
There is no forced expiration or first-login password change. Protect the file,
and retain it after a later creation failure because surviving resources may use
it. Retries require a new destination. Deleting a VM does not delete this file;
the operator owns cleanup. Guest-side password changes make the initial file
stale. Lost credentials require another authorized access path or an offline
reset; there is no password retrieval command.

Clones use the target definition's policy. An enabled target requires a new file:

```bash
sudo kvm-vm clone source target.yaml --console-password-file /root/vm-credentials/target.json
```

Each enabled clone receives an independent credential. Before boot, the cloned
disk's target management password and any known opted-in source management
password are locked offline; only the enabled target is subsequently unlocked
with its new credential. A disabled target remains locked. The source VM is
unchanged. Delivery failure leaves guest resources untouched; later failures
retain target resources and delivered credentials.

Reinstall preserves the saved console policy and network configuration. An
enabled VM requires a new destination and receives a new password:

```bash
sudo kvm-vm reinstall vm --yes --console-password-file /root/vm-credentials/vm-reinstalled.json
```

Credential validation and delivery finish before forced shutdown or disk
replacement. Existing credential files are never overwritten or removed. A
disabled or legacy VM remains password-locked and needs no destination.
`--dry-run` generates no credential. After replacement, the previous password
does not apply; verify guest provisioning and login with the newly delivered
credential. Failures after replacement retain surviving resources and cannot
promise restoration of the original disk.

## Security policy and out-of-band recovery

By default, cloud-init locks the management user's password (`lock_passwd: true`),
disables SSH password authentication (`ssh_pwauth: false`) and disables direct
root SSH access (`disable_root: true`). Console opt-in keeps SSH key-only and
unlocks the non-root management user only after the final SSH policy checks.
It does not set a root password or enable autologin. Custom images retain their
own local root-account policy.

For planned console access, use [console password login](#optional-console-password-login)
instead of placing passwords in YAML or `runcmd`. If no usable console credential
or SSH key remains, use the offline SSH-key recovery procedure below. Confirm the
VM is fully shut off before editing its disk.

### SSH key recovery

If you lose your private SSH key or need to authorize a new key without console access:

1. Shut down the VM:
   ```bash
   virsh shutdown web01
   ```

2. Inject the new public key directly into the VM disk using `virt-customize`:
   ```bash
   virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
   ```
   *(Or by disk path: `virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 --ssh-inject admin:file:~/.ssh/id_ed25519.pub`)*

3. Start the VM and log in with your new key:
   ```bash
   virsh start web01
   ssh admin@<vm-ip> -i /path/to/new_key
   ```

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

## Reinstall a VM

Reinstalling an existing managed VM provisions a fresh operating system disk from a base cloud image while preserving vCPUs, RAM, disk capacity, MAC address, IP configuration, cloud-init user/SSH keys, console policy and network attachment (bridge, NAT network or Macvtap parent). An enabled console password is rotated; it is not preserved.

```bash
# Reinstall using the existing OS from the VM definition
kvm-vm reinstall web01

# Reinstall and switch to a different operating system
kvm-vm reinstall web01 --os debian13
```

### Options

- `--console-password-file <path>`: Required when the saved console policy is enabled; deliver a new credential to a new private file before shutdown or disk replacement. See [console password login](#optional-console-password-login).
- `--os <distro|url|path>`: Distro alias (see [Distro aliases](#distro-aliases)), custom image URL, or local image path.
- `--image-url <url>`: Explicitly specify a cloud image URL.
- `--image-path <path>`: Explicitly specify a local cloud image path.
- `--os-variant <variant>`: Specify a libosinfo OS variant for `virt-install`.
- `--refresh-image`: Redownload the base cloud image even if already cached.
- `--no-start`: Reinstall and define the domain in libvirt without booting it.
- `--force`: If the VM is currently running, destroy it before reinstalling (without `--force`, a running VM refuses reinstallation).
- `--yes`: Skip interactive confirmation (required in non-interactive/automation environments).
- `--dry-run`: Run all pre-flight checks and display the resulting effective YAML definition without touching libvirt or disks.

```bash
# Automation example with different OS and force destroy
kvm-vm reinstall web01 --os rocky9 --force --yes
```

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
- The source system disk must be an independent regular qcow2 or raw file.
- Additional data disks are left untouched and are not copied to the clone.
  Recovery chains and active recovery work are refused before cloning.
- A smaller target virtual disk is refused. A larger target is allowed; cloud images or
  guest tooling are still responsible for expanding partitions/filesystems as appropriate.

### Verifying virt-sysprep compatibility

To test whether the host's `virt-sysprep` supports a target guest OS before cloning:

1. **Check supported operations on host**:
   ```bash
   virt-sysprep --list-operations
   ```
   `kvm-vm` requires at least `machine-id` and `ssh-hostkeys`.

2. **Test OS inspection**:
   `virt-sysprep` relies on `libguestfs` inspection. Verify that the guest OS layout is recognized:
   ```bash
   virt-inspector -a /path/to/image.qcow2
   ```
   A successful inspection outputs XML with `<operatingsystem>`, `<distro>`, `<version>`, and mountpoints.

3. **Simulate operations with `--dry-run`**:
   Run a non-destructive dry-run against the image using the exact operations executed by `kvm-vm`:
   ```bash
   virt-sysprep --dry-run -a /path/to/image.qcow2 \
     --operations machine-id,ssh-hostkeys,ssh-userdir,net-hostname,net-hwaddr,dhcp-client-state,random-seed
   ```
   If this succeeds without mounting errors or unrecognized OS warnings, `virt-sysprep` is fully compatible.

## CPU model note

The examples use:

```yaml
cpu: host-passthrough
```

This maximizes access to the host CPU features and is appropriate for a stable single-host
or homogeneous-host setup. If live migration between different CPU generations is a goal,
use a deliberately selected migratable CPU model instead.

## Base image refresh

Distribution cloud images downloaded during previous `create` runs are cached locally (by default in `/var/lib/libvirt/images/base/`). Subsequent VM creations reuse the cached image to avoid redundant downloads.

If upstream releases an update and you want to fetch the latest cloud image to create a new VM, specify the `--refresh-image` flag:

```bash
kvm-vm create newvm.yaml --refresh-image
```

- **Existing VMs remain unaffected**: This flag only updates the cached base image in the local storage pool. Because each VM runs on its own independent qcow2 disk, re-downloading or updating the base image will never affect already created VMs.
- **Unconditional re-download**: As confirmed from the source code, `--refresh-image` does not perform HTTP conditional checks (such as `ETag` or `Last-Modified`); whenever this flag is passed, the tool unconditionally re-downloads the complete image and overwrites the local cache, even if the cached image is already the latest version.

## Snapshots

`kvm-vm snapshot create NAME` keeps one short-lived rollback point before an
update, without stopping the VM; the guest filesystems are frozen briefly
through the QEMU guest agent. `kvm-vm snapshot status NAME` shows it, and
`kvm-vm snapshot delete NAME --yes` removes it while keeping the current data.
If the update went wrong, `kvm-vm snapshot rollback NAME --yes --force` returns
to the captured disk, VM configuration and kvm-vm files and starts the VM again,
keeping its identity. A Snapshot is not a Backup, and it is never combined with
incremental Backup tracking: retire the tracking before the Snapshot and start
a new full Backup after it, as described in
[Snapshots and Backups](docs/snapshots.md#snapshots-and-backups). Supported
configurations, pauses, failure recovery and AppArmor behavior are in
[docs/snapshots.md](docs/snapshots.md).

## Recovery state safety

Clone, reinstall and delete refuse checkpoints, snapshots, backing chains,
inconsistent disk paths and active recovery jobs. They support an independent
qcow2 or raw system file and additional file/block data disks. Reinstall keeps
data disk attachments; clone copies only the system disk. `delete --keep-storage`
also permits a block-backed system disk. Failed create/clone operations retain surviving
resources for inspection; retrying does not overwrite existing artifacts.
If a VM is absent and only its managed state/definition remain, `create` can
reuse the name. It checks both current and recorded disk/cloud-init paths,
including conversion temporary files, and replaces the state only on success.
Existing data still blocks creation; the error identifies the path to inspect.
See [lifecycle recovery safety](docs/lifecycle-recovery-safety.md) for dependency
checks, keep-storage behavior, operation locking and external-tool sequencing.

The [Backup/Restore procedure](docs/full-backup-restore.md) and
[two-host acceptance workflow](docs/full-backup-acceptance.md) passed one
cross-host exercise (Debian 13 to AlmaLinux 9.8, one guest, synced-marker
workload) for a full Backup and for chosen points of a full plus incremental
history. Backup and Restore use virtnbdbackup and virtnbdrestore directly, and
other guests, applications and host combinations are untested. Before any
restore, run `kvm-vm backup check DIR [--until virtnbdbackup.N]`:
`virtnbdrestore` can exit 0 while restoring an incomplete or reordered history
(see [ADR 0004](docs/adr/0004-pre-restore-backup-check.md)). See the
[ticket 03](docs/plans/20261018_vm-recovery/ticket-03-validation.md) and
[ticket 04](docs/plans/20261018_vm-recovery/ticket-04-validation.md) evidence.

## `--no-start`

`create`, `clone`, and `reinstall` support:

```bash
kvm-vm create vm.yaml --no-start
kvm-vm reinstall web01 --no-start
```

The domain is defined but left shut off, which is useful if you want to inspect `virsh
dumpxml` or apply additional libvirt policy before first boot.

## `--dry-run`

`create`, `clone`, and `reinstall` support:

```bash
kvm-vm create vm.yaml --dry-run
kvm-vm clone source-vm target.yaml --dry-run
kvm-vm reinstall web01 --os debian13 --dry-run
```

Runs all pre-flight checks (YAML normalization, domain/state collision checks, bridge/NAT/Macvtap
validation, MAC allocation, and SSH key resolution) and prints the normalized YAML without
downloading images, touching disks, or defining libvirt domains. For `clone`, it also verifies
`virt-sysprep` compatibility against the source disk using a dry-run check before any disk conversion.

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

## Using newer or custom Linux distributions

`kvm-vm` is not restricted to the built-in distro aliases. Newer releases (such as Ubuntu 26.04, Rocky Linux 10, AlmaLinux 10) or custom images can be supported in two ways:

### 1. Direct URL or local file path (no code changes needed)

Any cloud-init compatible qcow2/raw image can be used immediately by providing `url` or `path`:

```yaml
vm:
  name: web01
  # Optional: specify os_variant for virt-install optimization.
  # If the host libosinfo lacks the exact new release, specify a preceding release or omit it.
  os_variant: ubuntu24.04

storage:
  disk_gib: 40
  image:
    url: https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img
    # or local path:
    # path: /var/lib/libvirt/images/base/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2
```

### 2. Adding new distro aliases to `kvm-vm`

To enable convenient shorthand for other distributions (e.g. `distro: fedora41`), add an entry to the `DISTROS` dictionary in `kvm-vm`:

```python
DISTROS = {
    ...
    "fedora41": {
        "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/41/Cloud/x86_64/images/Fedora-Cloud-Base-Generic.x86_64-41-1.4.qcow2",
        "cache": "Fedora-Cloud-Base-Generic.x86_64-41-1.4.qcow2",
        "os_variant": "fedora41",
    },
}
```

Key considerations for newer distros:
- **`os_variant` & `libosinfo` compatibility**:
  - **Automatic fallback to `generic`**: `kvm-vm` automatically probes `osinfo-query os` before provisioning. If a specified `os_variant` is not recognized by the host's `libosinfo`, it logs a warning and gracefully falls back to `--os-variant generic` rather than failing.
  - **Negligible impact on headless VMs**: Because `kvm-vm` explicitly configures headless console access (`--graphics none`), `virtio` disk bus, and `virtio` network interfaces, falling back to `generic` does not prevent the VM from booting or degrade standard server performance.
  - **Recommendation**: To leverage tuned hardware topology profiles for newer distros before host `libosinfo` packages are updated, explicitly set `vm.os_variant` to the closest preceding major release (e.g., `rocky9` for Rocky Linux 10, or `ubuntu24.04` for Ubuntu 26.04).
- **Sysprep support for cloning**: If using `kvm-vm clone`, ensure the host's `libguestfs-tools` / `virt-sysprep` version supports the guest OS layout (machine-id, network configs, etc.). See [Verifying virt-sysprep compatibility](#verifying-virt-sysprep-compatibility) for how to test.

## What this tool deliberately does not do

It is not a replacement for a cluster manager. It does not currently manage:

- live migration
- Ceph/RBD storage
- multiple or long-term snapshots, or backups (it keeps one short-lived
  [Snapshot](docs/snapshots.md); Backups use the external
  [procedure](docs/full-backup-restore.md))
- multiple NICs
- multiple managed data disks
- VLAN tagging
- Windows sysprep
- reconciliation/drift correction of an already-created VM

Those can be added later without changing the basic YAML/state model.

## Testing

### Prerequisites

```bash
pip install pytest
```

### Unit tests

Unit tests cover pure-Python functions (YAML normalization, MAC generation, SSH key
resolution, download retry logic, CLI argument parsing) and do not require libvirt or
root privileges.

```bash
python3 -m pytest tests/test_unit.py -v
```

### Smoke tests

The smoke test script compiles the main script, runs `--help` / `--version`, executes
the unit tests if pytest is available, and validates the example YAML files against the
normalizer. It does not require a running libvirt daemon.

```bash
bash tests/smoke.sh
```

### End-to-end (E2E) tests

Real-machine E2E tests execute actual VM provisioning against libvirt/KVM:
- Download cloud base image (default: Debian 12)
- Generate cloud-init seed ISO and deploy VM with NAT networking
- Wait for DHCP lease IP and verify SSH connectivity, hostname, and user creation
- Verify `kvm-vm status` and `kvm-vm list` outputs
- Offline VM cloning with `virt-sysprep` and verify cloned guest connectivity
- Custom SSH port, its reset to port 22 on a clone, and rollback when the new port is blocked
- In-place OS reinstallation with `--force --yes`
- Complete deletion and storage cleanup verification

**Prerequisites:**
- Linux host with hardware virtualization (`/dev/kvm`)
- `root` privileges (run with `sudo`)
- Running libvirt daemon with active `default` NAT virtual network
- Required CLI tools (`virsh`, `virt-install`, `qemu-img`, `virt-sysprep`, `cloud-localds`/`genisoimage`/`xorriso`, `ssh`)

Run the pre-flight checks and E2E test suite:

```bash
sudo bash tests/run_e2e.sh
```

Or target a specific distro:

```bash
sudo bash tests/run_e2e.sh --distro ubuntu24.04 -v
```

These tests wait 180 seconds for SSH. Rocky/Alma guests install SELinux tools at first
boot and may need longer on slow hosts, so raise the wait with `KVM_VM_E2E_SSH_TIMEOUT`:

```bash
sudo KVM_VM_E2E_SSH_TIMEOUT=600 bash tests/run_e2e.sh --distro rocky8 -v
```

To check `create` and SSH access for every declared distro alias:

```bash
sudo python3 -m pytest tests/test_distro_e2e.py -v -s
```

Each case creates a VM with 2 vCPUs, 2048 MiB RAM, a 20 GiB disk, NAT networking,
and SSH port 2222, then checks key login, the actual OS version, and completion of
cloud-init. Rocky/Alma cases configure SELinux and any active firewalld before SSH
restarts. Temporary VMs are cleaned up even when a case fails.

The host CPU must meet the guest image's requirements: Rocky/AlmaLinux 9 require
x86-64-v2, and the default Rocky/AlmaLinux 10 images require x86-64-v3. KVM with
`host-passthrough` cannot provide instructions missing from the physical CPU.
An SSH timeout on incompatible hardware is a failed verification, not a pass.

Use `KVM_VM_E2E_DISTROS` to select comma-separated aliases and
`KVM_VM_E2E_SSH_TIMEOUT` to change the 600-second provisioning wait:

```bash
sudo KVM_VM_E2E_DISTROS=ubuntu26.04,debian13,rocky8,rocky9,rocky10,almalinux8,almalinux9,almalinux10 \
  python3 -m pytest tests/test_distro_e2e.py -v -s
```

See [the real-machine verification report](tests/REAL_MACHINE_RESULTS.md) for
the tested images, boot fix, and hardware limitations of the verification host.

### Macvtap and console acceptance

The [Macvtap workflow](tests/MACVTAP_ACCEPTANCE.md) and
[console workflow](tests/CONSOLE_ACCEPTANCE.md) describe opt-in real-machine tests.
They require an authorized KVM host; bridge/Macvtap SSH checks need an independent
client that can reach the guest. Console tests additionally use Paramiko on the
controller and pexpect on the Linux host; these are not product dependencies.

Console login, sudo, key-only SSH and reboot checks passed on Debian 12/13,
Ubuntu 24.04/26.04, Rocky Linux 8 and AlmaLinux 8. The matrix also covers locked
defaults across all three modes, clone credential isolation and reinstall rotation.
Rocky/AlmaLinux 9/10 console acceptance remains pending compatible hardware; the
complete image matrix is not yet accepted. See the
[console validation report](docs/plans/20261019_macvtap-console/ticket-06-validation.md)
for exact coverage, retained failures and successful continuations.
