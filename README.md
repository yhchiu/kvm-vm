English | [繁體中文](README.zh-TW.md)

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
kvm-vm reinstall <vm> [--os <os>] [--image-url <url>] [--image-path <path>] [--refresh-image] [--no-start] [--dry-run] [--yes] [--force]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
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
  guestfs-tools libosinfo-bin iproute2 openssh-client
```

*(Note: On Debian 11 or older releases, use `virtinst` instead of `virt-install`, and `libguestfs-tools` instead of `guestfs-tools`)*

RHEL / Rocky / Alma family:

```bash
dnf install \
  qemu-kvm qemu-img libvirt virt-install \
  xorriso python3 python3-pyyaml \
  guestfs-tools libosinfo iproute openssh-clients

# Start and enable libvirt daemon (RHEL does not start services automatically)
sudo systemctl enable --now libvirtd
```

*(Note: On RHEL 8 / Rocky 8 or older releases, use `genisoimage` instead of `xorriso`, and `libguestfs-tools` instead of `guestfs-tools`)*

For building cloud-init seed images, `kvm-vm` automatically detects and uses any available tool: `cloud-localds` (from `cloud-image-utils` / `cloud-utils`), `genisoimage`, `mkisofs`, or `xorriso`.

`virt-sysprep` and `virt-customize` are needed only for `clone`, but installing them on
a management host is recommended.

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

> [!NOTE]
> **SSH key-only security policy**:
> `kvm-vm` provisions instances with SSH password authentication and direct root login disabled (`lock_passwd: true`, `ssh_pwauth: false`, `disable_root: true`).
> See [Security policy & out-of-band recovery](#security-policy-and-out-of-band-recovery) for details on root password configuration, console login, and emergency SSH key recovery.

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

## Environment variables

All storage paths, libvirt connection URI, and VM definition values can be overridden with environment variables (taking precedence over YAML files and built-in defaults):

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
| `KVM_VM_OS` | `image.distro` / `os_variant` | `rocky9` | Overrides distro alias and `os_variant` |
| `KVM_VM_BRIDGE` | `network.bridge` | `br0` | Default or override bridge interface for bridge mode |
| `KVM_VM_IPV4` | `network.ipv4` | `dhcp`, `192.168.1.50/24` | Sets method (`dhcp`, `disabled`) or static CIDR address |
| `KVM_VM_GATEWAY` | `network.ipv4.gateway` | `192.168.1.1` | Overrides default IPv4 gateway |
| `KVM_VM_DNS` | `network.ipv4.dns` | `1.1.1.1,8.8.8.8` | Overrides DNS servers (comma-separated) |

## Network modes

`kvm-vm` supports two single-NIC attachment modes.

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

> **Note**: As detailed in [Security policy](#security-policy-and-out-of-band-recovery), user passwords are locked by default (`lock_passwd: true`). If you need to log into the console via keyboard, see [Root password configuration](#root-password-configuration) below.

## Security policy and out-of-band recovery

By default, `kvm-vm` provisions instances with a strict production-grade security policy via cloud-init:
- **SSH key-only**: `ssh_pwauth: false` disables password login over SSH.
- **Locked password**: `lock_passwd: true` locks the default user account password.
- **Root login disabled**: `disable_root: true` prevents direct root login.

Because passwords are locked by default, you **cannot** log into the serial console (`kvm-vm console <vm>`) with a password out-of-the-box. When you need emergency console access or need to recover from lost credentials, use the out-of-band methods below.

### Root password configuration

To configure a root password for local serial console login:

1. **Recommended method (out-of-band via `virt-customize`)**:
   Shut down the VM, then inject the root password from standard input. This is strongly recommended because it avoids leaking plaintext passwords in definition YAML files, shell history (`~/.bash_history`), or process tables (`ps`):

   ```bash
   # 1. Shut down the VM first
   virsh shutdown web01

   # 2. Securely inject root password from stdin
   virt-customize -d web01 --root-password file:/dev/stdin
   # (enter your password and press Enter, then Ctrl+D)
   ```

   Alternatively, pass a secured temporary file or target the disk image directly:

   ```bash
   virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 --root-password file:/path/to/file
   ```

2. **During initial creation (via `runcmd`)**:
   You can also set a password during creation in the YAML definition (note: the password is stored in plaintext in the YAML file):

   ```yaml
   cloud_init:
     user: admin
     ssh_authorized_keys:
       - file:~/.ssh/id_ed25519.pub
     runcmd:
       - "echo 'root:YourPasswordHere' | chpasswd"
   ```

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

Reinstalling an existing managed VM provisions a fresh operating system disk from a base cloud image while preserving all other VM configurations (vCPUs, RAM, disk capacity, assigned MAC address, static/DHCP IP, cloud-init user/SSH keys, network mode/bridge):

```bash
# Reinstall using the existing OS from the VM definition
kvm-vm reinstall web01

# Reinstall and switch to a different operating system
kvm-vm reinstall web01 --os debian13
```

### Options

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
- The system disk must currently be a regular file-backed disk.
- Only the system disk is cloned; separately attached data disks are not cloned.
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

Runs all pre-flight checks (YAML normalization, domain/state collision checks, bridge/NAT
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
- snapshots/backups
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

