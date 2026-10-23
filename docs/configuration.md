English | [繁體中文](configuration.zh-TW.md)

# VM definition reference

A VM definition is a YAML file that describes one VM. `validate`, `create` and
`clone` read it, fill in defaults, and `create`/`clone` save the result as the
VM's **effective definition** in `/etc/kvm-vm/definitions/NAME.yaml`.

- [Minimal definition](#minimal-definition)
- [Full example](#full-example)
- [Field reference](#field-reference)
- [OS images](#os-images)
- [SSH keys and port](#ssh-keys-and-port)
- [Environment overrides](#environment-overrides)
- [Host paths](#host-paths)
- [Keeping definitions in Git](#keeping-definitions-in-git)

Network fields are explained in [Networking](networking.md).

## Minimal definition

Only three things are required: a VM name, an image (for `create`) and at least
one SSH public key.

```yaml
vm:
  name: test01
storage:
  image:
    distro: debian13
cloud_init:
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
```

Everything else gets a default: 2 vCPUs, 2048 MiB of memory, a 20 GiB disk, the
user `admin`, and DHCP on the host bridge `br0`. To see the complete result, run:

```bash
kvm-vm validate test01.yaml
```

`validate` works offline. It does not need root, libvirt, or a running daemon.

## Full example

This is [`examples/ubuntu24-web01.yaml`](../examples/ubuntu24-web01.yaml), a VM
with a static address on bridge `br0`:

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

Other examples: [`debian13-dhcp.yaml`](../examples/debian13-dhcp.yaml) (bridge
and DHCP), [`ubuntu24-nat.yaml`](../examples/ubuntu24-nat.yaml) (libvirt NAT)
and [`clone-target.yaml`](../examples/clone-target.yaml) (target of `clone`).

## Field reference

`version` is optional and must be `1`.

### `vm`

| Field | Default | Notes |
|---|---|---|
| `name` | required | libvirt domain name and guest hostname. Letters, digits, `.`, `_` and `-`, up to 63 characters, starting with a letter or digit. |
| `vcpus` | `2` | 1 to 1024. |
| `memory_mib` | `2048` | At least 256. |
| `cpu` | `host-passthrough` | Passed to `virt-install --cpu`. See [CPU model](#cpu-model). |
| `autostart` | `true` | Start the VM when the host boots. |
| `start` | `true` | Boot the VM after `create` or `clone`. `--no-start` overrides this for one run. |
| `os_variant` | from the image alias, otherwise `generic` | libosinfo name used by `virt-install`. See [os_variant](#os_variant). |

### `storage`

| Field | Default | Notes |
|---|---|---|
| `disk_gib` | `20` for `create`; source size for `clone` | System disk size in GiB. It cannot be smaller than the image (or, for `clone`, the source disk). |
| `bus` | `virtio` | |
| `cache` | `none` | |
| `discard` | `unmap` | |
| `image.distro` | | A [built-in alias](#built-in-aliases). |
| `image.url` | | Download a cloud image. |
| `image.path` | | Use a local cloud image. |
| `image.sha256` | none | Optional checksum of the image. A mismatch stops the command. |

`create` needs exactly one of `image.distro`, `image.url` or `image.path`.
`clone` ignores `image`, because the source VM's disk is the image.

Every VM gets its own full qcow2 copy of the image. Refreshing or deleting a
cached image never affects existing VMs.

### `network`

| Field | Default | Notes |
|---|---|---|
| `mode` | `bridge` | `bridge`, `nat` or `macvtap`. See [Networking](networking.md). |
| `bridge` | `br0` | Bridge mode only. |
| `libvirt_network` | `default` | NAT mode only. |
| `parent` | required for Macvtap | Macvtap mode only: the host's physical interface, such as `eno1`. |
| `model` | `virtio` | |
| `mac` | `auto` | `auto` picks an unused `52:54:00:xx:xx:xx` address and saves it in the effective definition. |
| `ipv4.method` | `dhcp` | `dhcp`, `static` or `disabled`. |
| `ipv4.address` | | Static only, with a prefix, such as `192.168.10.51/24`. |
| `ipv4.gateway` | | Required for static. |
| `ipv4.dns` | | List of DNS server addresses. |
| `ipv6.method` | `disabled` | `dhcp` and `static` (with `address` and `gateway`) are also accepted. IPv6 values are passed to cloud-init without checks. |

cloud-init finds the guest's network card by its MAC address and renames it to
`eth0`. The definition therefore works the same whether the image would call the
card `ens3`, `enp1s0` or something else.

### `cloud_init`

| Field | Default | Notes |
|---|---|---|
| `user` | `admin` | Non-root management user. It gets passwordless `sudo`. |
| `ssh_authorized_keys` | required | At least one key. See [SSH keys](#ssh-keys). |
| `ssh_port` | `22` | 1 to 65535. See [SSH port](#ssh-port). |
| `console_password` | `false` | `true` creates a random console password. See [Console password login](access.md#console-password-login). |
| `qemu_guest_agent` | `true` | Installs and starts `qemu-guest-agent` and adds its virtio channel. Needed for `list --ips` on some networks and for Snapshot filesystem freezes. |
| `package_update` | `false` | Refresh the package index at first boot (cloud-init `package_update`). |
| `packages` | `[]` | Packages to install at first boot. |
| `timezone` | image default | Such as `Asia/Taipei`. |
| `runcmd` | `[]` | cloud-init `runcmd` entries. They run before kvm-vm's own first-boot steps (SSH port, console password). |

The user's password is locked and SSH accepts keys only. See
[Access and security](access.md).

### CPU model

`cpu: host-passthrough` gives the guest every feature of the host CPU. It is the
right choice for a single host or identical hosts. If you plan live migration
between different CPU generations, choose a migratable CPU model instead.

A guest cannot use instructions that the physical CPU lacks. Rocky Linux and
AlmaLinux 9 need an x86-64-v2 CPU, and their version 10 images need x86-64-v3. On
an older CPU these guests are created but do not finish booting.

## OS images

### Built-in aliases

| Alias | `os_variant` used | Create and SSH verified |
|---|---|---|
| `ubuntu24.04` | `ubuntu24.04` | Yes |
| `ubuntu26.04` | `ubuntu26.04` | Yes |
| `debian12` | `debian12` | Yes |
| `debian13` | `debian13` | Yes |
| `rocky8` | `rocky8` | Yes |
| `rocky9` | `rocky9` | Yes (x86-64-v2 host) |
| `rocky10` | `rocky9` | Not yet: needs an x86-64-v3 host |
| `almalinux8` | `almalinux8` | Yes |
| `almalinux9` | `almalinux9` | Yes (x86-64-v2 host) |
| `almalinux10` | `almalinux9` | Not yet: needs an x86-64-v3 host |

Aliases download the vendor's moving `current` or `latest` cloud image, so the
image can change between runs. The test runs and their exact images are recorded
in [tests/REAL_MACHINE_RESULTS.md](../tests/REAL_MACHINE_RESULTS.md).

### Your own image

Any cloud-init capable qcow2 or raw image works, with no code change:

```yaml
storage:
  image:
    url: https://images.example.com/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

or

```yaml
storage:
  image:
    path: /srv/images/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

For repeatable production builds, a pinned URL or local file plus `sha256` is
better than a moving alias.

### Image cache and `--refresh-image`

Downloaded images are cached in `/var/lib/libvirt/images/base/` and reused by
later `create` and `reinstall` runs. Pass `--refresh-image` to download again:

```bash
sudo kvm-vm create newvm.yaml --refresh-image
```

- The download always happens: there is no `ETag` or `Last-Modified` check.
- Existing VMs are not affected, because each VM has its own disk.
- `image.url` images are cached by the file name at the end of the URL. Two
  URLs that end in the same file name share one cache file.
- `image.path` images are used in place and never cached.

### `os_variant`

`virt-install` uses `os_variant` to pick tuned virtual hardware defaults. Before
creating a VM, kvm-vm checks the name with `osinfo-query os`. If the host's
libosinfo does not know it, kvm-vm logs a warning and uses `generic`. The VM
still boots normally, because kvm-vm always uses virtio disks and network cards
and a serial console. To keep tuned defaults on an older host, set the closest
older release yourself, such as `rocky9` for Rocky Linux 10.

### Adding an alias

To add a short name such as `distro: myos1`, add an entry to `DISTROS` in the
`kvm-vm` script, then run `sudo ./install.sh` again:

```python
DISTROS = {
    ...
    "myos1": {
        "url": "https://images.example.com/myos-1-cloud.x86_64.qcow2",
        "cache": "myos-1-cloud.x86_64.qcow2",  # file name in the image cache
        "os_variant": "generic",               # a name that osinfo-query knows
    },
}
```

Keep such changes in your own branch, because installing a new kvm-vm version
replaces the script. If you plan to `clone` these VMs, check that the host's
`virt-sysprep` understands the guest; see
[Check virt-sysprep support](commands.md#check-virt-sysprep-support).

## SSH keys and port

### SSH keys

Each entry in `ssh_authorized_keys` is either a full OpenSSH public key or
`file:` followed by a path to a `.pub` file:

```yaml
cloud_init:
  ssh_authorized_keys:
    - ssh-ed25519 AAAAC3Nza... me@laptop
    - file:~/.ssh/id_ed25519.pub
    - file:keys/ops.pub
```

- A relative `file:` path is relative to the YAML file.
- `~` is your home directory. Under `sudo`, it is the home directory of the user
  who ran `sudo` (`$SUDO_USER`), not root's.

### SSH port

`ssh_port` sets the port the guest's SSH server listens on. With the default
`22`, kvm-vm leaves the image's SSH settings alone.

With another port, such as `ssh_port: 2222`, kvm-vm changes SSH at first boot,
after your `runcmd` commands:

- It replaces active `Port` lines in `/etc/ssh/sshd_config` and
  `/etc/ssh/sshd_config.d/*.conf`, checks the SSH configuration, and restarts SSH.
- Where `ssh.socket` starts SSH, as on Ubuntu, it also writes
  `/etc/systemd/system/ssh.socket.d/zz-kvm-vm-port.conf`. Edit or remove that
  file if you change the port by hand later.
- If SSH cannot start on the new port (for example, SELinux or another service
  blocks it), kvm-vm puts the old SSH files back and restarts SSH. The guest stays
  reachable on its previous port (22 for a new VM), cloud-init reports an error,
  and `/var/log/cloud-init-output.log` shows
  `kvm-vm: could not move SSH to port <port>`.

Allow the port through the guest's firewall and any upstream firewall. On
SELinux guests such as Rocky Linux and AlmaLinux, allow a port that the policy
does not already assign to SSH. Merge this into your `cloud_init` section:

```yaml
cloud_init:
  ssh_port: 2222
  packages:
    - policycoreutils-python-utils
  runcmd:
    - [semanage, port, -a, -t, ssh_port_t, -p, tcp, '2222']
```

How other commands treat the port:

- `clone` uses the target definition's port, or 22 when it is not set. With 22,
  it removes the source guest's `Port` lines and socket file so SSH returns to
  the image default.
- `reinstall` keeps the saved port.
- Editing a definition does not change a running VM.

## Environment overrides

Environment variables can change some definition values at run time. They take
priority over the YAML for `validate`, `create` and `clone`. `reinstall` always
uses the saved definition and ignores them.

```bash
sudo KVM_VM_OS=debian13 KVM_VM_NAME=web02 KVM_VM_VCPUS=16 KVM_VM_DISK=100 \
  KVM_VM_MEMORY=32000 KVM_VM_IPV4=192.168.10.52/24 \
  kvm-vm create examples/ubuntu24-web01.yaml
```

This uses `ubuntu24-web01.yaml` as a template and creates a different VM, `web02`,
without writing a new file.

| Variable | Changes | Example | Notes |
|---|---|---|---|
| `KVM_VM_NAME` | `vm.name` | `web02` | |
| `KVM_VM_VCPUS` | `vm.vcpus` | `4` | 1 to 1024. |
| `KVM_VM_MEMORY` or `KVM_VM_MEMORY_MIB` | `vm.memory_mib` | `4096` | At least 256. |
| `KVM_VM_DISK` or `KVM_VM_DISK_GIB` | `storage.disk_gib` | `50` | At least 1. |
| `KVM_VM_OS` | `image.distro` and `vm.os_variant` | `rocky9` | Must be a [built-in alias](#built-in-aliases). Sets `os_variant` to the alias name. For `clone`, sets only `os_variant`. |
| `KVM_VM_BRIDGE` | `network.mode`, `network.bridge` | `br0` | Selects bridge mode. Cannot be combined with Macvtap. |
| `KVM_VM_MACVTAP` | `network.mode`, `network.parent` | `eno1` | Selects Macvtap mode, even for a bridge or NAT definition. An empty value is an error. |
| `KVM_VM_IPV4` | `network.ipv4` | `dhcp` or `192.168.1.50/24` | `dhcp`, `disabled`, or a static address with a prefix. |
| `KVM_VM_GATEWAY` | `network.ipv4.gateway` | `192.168.1.1` | |
| `KVM_VM_DNS` | `network.ipv4.dns` | `1.1.1.1,8.8.8.8` | Comma-separated. |

Console password login cannot be turned on from the environment. It is set in the
YAML, and the credential file is given with `--console-password-file`.

## Host paths

`install.sh` creates these directories. Each can be moved with an environment
variable.

| Variable | Default | Contents |
|---|---|---|
| `KVM_VM_DEF_DIR` | `/etc/kvm-vm/definitions` | Effective definitions, `NAME.yaml`. |
| `KVM_VM_STATE_DIR` | `/var/lib/kvm-vm/state` | State manifests, Snapshot records (`snapshots/NAME/`) and the lifecycle lock (`.lifecycle-lock`). |
| `KVM_VM_BASE_DIR` | `/var/lib/libvirt/images/base` | Cached cloud images. |
| `KVM_VM_DISK_DIR` | `/var/lib/libvirt/images/vm` | VM system disks, `NAME.qcow2`. |
| `KVM_VM_CLOUD_DIR` | `/var/lib/libvirt/images/cloud-init` | Per-VM cloud-init seed and source files. |
| `KVM_VM_LIBVIRT_URI` | `qemu:///system` | libvirt connection. |

The disk, image and cloud-init directories sit under `/var/lib/libvirt/images`, so
the usual libvirt/QEMU ownership and SELinux labels apply. kvm-vm runs
`restorecon` on new files when it is available.

Every kvm-vm command that manages the same libvirt instance must use the same
values. The lifecycle lock lives in the state directory, so two processes with
different state directories do not see each other.

## Keeping definitions in Git

Keep the YAML files you write in Git, for example:

```text
infra/kvm/
  web01.yaml
  web02.yaml
  db01.yaml
```

```bash
kvm-vm validate infra/kvm/web01.yaml
sudo kvm-vm create infra/kvm/web01.yaml
sudo kvm-vm status web01
```

Treat `/etc/kvm-vm/definitions/*.yaml` as the record of what was deployed, not as
the source of truth. It contains the exact values used on the host, including
the generated MAC address. Editing it does not change the VM; `reinstall` reads it
to rebuild the VM with the same settings.
