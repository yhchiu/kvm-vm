English | [繁體中文](README.zh-TW.md)

# kvm-vm

`kvm-vm` creates and manages VMs on a single KVM/libvirt host from small
YAML files. It uses the standard tools (`virt-install`, `virsh`, `qemu-img`,
cloud-init) and keeps two records for every VM it creates: the effective
definition and a state manifest. Later commands such as `reinstall`, `clone` and
`delete` use these records, so they never have to guess how a VM was built.

It replaces hand-written `virt-install` command lines, not OpenStack or Proxmox.
See [What kvm-vm does not do](#what-kvm-vm-does-not-do).

- [Quick start](#quick-start)
- [Everyday commands](#everyday-commands)
- [Documentation](#documentation)
- [Safety rules](#safety-rules)
- [Requirements](#requirements)
- [What kvm-vm does not do](#what-kvm-vm-does-not-do)

## Quick start

These steps create a first VM on libvirt's `default` NAT network, which needs no
changes to the host's network. Run them on the KVM host.

**1. Install the host packages.**

Debian / Ubuntu:

```bash
sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
  virt-install cloud-image-utils python3 python3-yaml \
  guestfs-tools libosinfo-bin iproute2 openssh-client openssl
```

RHEL / Rocky Linux / AlmaLinux 9 or later:

```bash
sudo dnf install qemu-kvm qemu-img libvirt virt-install xorriso python3 python3-pyyaml \
  guestfs-tools libosinfo iproute openssh-clients openssl
for drv in qemu network nodedev nwfilter secret storage interface; do
  sudo systemctl start virt${drv}d{,-ro,-admin}.socket
done
```

RHEL 9 and later use libvirt's modular daemons (`virtqemud` and others), which
are enabled when the packages are installed. The loop, from Red Hat's
documentation, starts them without a reboot. Do not also enable `libvirtd`: it
conflicts with the modular daemons.

For older releases, see [Requirements](#requirements).

**2. Install kvm-vm** from a checkout of this repository:

```bash
sudo ./install.sh
```

This installs `/usr/local/sbin/kvm-vm` and creates its directories. Run it again
to upgrade.

**3. Make sure you have an SSH key.** The examples use `~/.ssh/id_ed25519.pub`.
If it does not exist, create it with `ssh-keygen -t ed25519`.

**4. Make sure the NAT network is running.**

```bash
sudo virsh net-list --all
```

If `default` is not `active`, start it and turn on autostart:

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

**5. Create the VM.** [`examples/ubuntu24-nat.yaml`](examples/ubuntu24-nat.yaml)
describes `nat01`, an Ubuntu 24.04 VM with 2 vCPUs, 4 GiB of memory and a 40 GiB
disk.

```bash
sudo kvm-vm validate examples/ubuntu24-nat.yaml          # check the file, show all values
sudo kvm-vm create examples/ubuntu24-nat.yaml --dry-run  # also check the host
sudo kvm-vm create examples/ubuntu24-nat.yaml
```

The first run downloads the Ubuntu cloud image, which can take a few minutes.

**6. Connect.** cloud-init sets up the guest after it starts, which takes a
minute or two.

```bash
sudo kvm-vm list --ips    # find the address
ssh admin@<vm-ip>         # admin is cloud_init.user
```

To remove the test VM: `sudo kvm-vm delete nat01 --force --yes`.

**Next steps:** write your own definition with the
[VM definition reference](docs/configuration.md), and put VMs on your LAN with
bridge or Macvtap mode ([Networking](docs/networking.md)).

## Everyday commands

Run these as root, for example with `sudo`. `kvm-vm COMMAND --help` lists every
option, and the [command reference](docs/commands.md) explains them.

| Task | Command |
|---|---|
| Check a definition (offline) | `kvm-vm validate vm.yaml` |
| Preview a create on this host | `kvm-vm create vm.yaml --dry-run` |
| Create a VM | `kvm-vm create vm.yaml` |
| List VMs with their addresses | `kvm-vm list --ips` |
| Show one VM in detail | `kvm-vm status web01` |
| Open the serial console (leave with `Ctrl+]`) | `kvm-vm console web01` |
| Reinstall the OS, keeping the settings | `kvm-vm reinstall web01 [--os debian13]` |
| Clone a shut-off VM | `kvm-vm clone web01 web02.yaml` |
| Delete a VM and the files kvm-vm made | `kvm-vm delete web01` |
| Remove a VM from libvirt but keep its files | `kvm-vm delete web01 --keep-storage` |
| Take a rollback point before a change | `kvm-vm snapshot create web01` |
| Keep the change | `kvm-vm snapshot delete web01 --yes` |
| Undo the change | `kvm-vm snapshot rollback web01 --yes --force` |
| Check a Backup before a Restore | `kvm-vm backup check /backups/web01` |

Common options: `--dry-run` checks without changing anything, `--yes` skips the
prompt (for scripts), `--force` powers off a running VM first, and `--no-start`
leaves the VM shut off.

## Documentation

| I want to... | Read |
|---|---|
| Write a VM definition: fields, defaults, images, overrides | [VM definition reference](docs/configuration.md) |
| Choose between bridge, NAT and Macvtap | [Networking](docs/networking.md) |
| Look up what a command does | [Command reference](docs/commands.md) |
| Log in on the console, or regain access | [Access and security](docs/access.md) |
| Use Snapshots safely | [Snapshots](docs/snapshots.md) |
| Back up a VM and restore it on another host | [Backup and Restore](docs/full-backup-restore.md) |
| Understand why clone, reinstall or delete refused | [Lifecycle operations around recovery state](docs/lifecycle-recovery-safety.md) |
| Run the tests | [Testing](tests/README.md) |
| Look up a term such as Snapshot or Backup | [Glossary](GLOSSARY.md) |
| Read why it is designed this way | [Architecture decisions](docs/adr/) |

## Safety rules

- **`delete` removes only what kvm-vm created**: the system disk, the cloud-init
  files, the definition and the state. Data disks you attached are never deleted.
- **`reinstall` erases the system disk.** The settings and data disks are kept.
- **`clone` works only on a shut-off VM** and copies only its system disk.
- **Destructive commands ask first.** `delete` and `reinstall` ask you to type
  the VM name unless you pass `--yes`, and refuse a running VM unless you pass
  `--force`.
- **A Snapshot is not a Backup.** It is one short-lived rollback point on the same
  storage. Use the [Backup procedure](docs/full-backup-restore.md) to survive the
  loss of the host.
- **Passwords are locked and SSH is key-only.** Console password login is opt-in.
- **With Macvtap, the host cannot reach its own VM over the network.** Use another
  machine or the console.

## Requirements

- A Linux host with KVM (`/dev/kvm`) and libvirt, used through `qemu:///system`.
  kvm-vm is tested on Debian 13 and AlmaLinux 9.8 hosts.
- Python 3.8 or later with PyYAML. libvirt's Python bindings are not needed:
  kvm-vm runs `virsh`, `virt-install` and `qemu-img`.
- A tool to build the cloud-init seed image: `cloud-localds`, `genisoimage`,
  `mkisofs`, `xorrisofs` or `xorriso`. kvm-vm uses the first one it finds.
- For `clone`: `virt-sysprep` and `virt-customize` (package `guestfs-tools`).
- For Macvtap: `ip` (package `iproute2` or `iproute`).
- For console password login: `openssl`.
- Recommended: `osinfo-query` (package `libosinfo-bin` or `libosinfo`). Without
  it, every VM uses the `generic` OS variant.

Older releases:

- Debian 11 and older: install `virtinst` instead of `virt-install`, and
  `libguestfs-tools` instead of `guestfs-tools`.
- RHEL 8 family: the default `python3` is 3.6, which is too old for kvm-vm, and
  these hosts are not tested. The package names also differ: `genisoimage`
  instead of `xorriso`, and `libguestfs-tools` instead of `guestfs-tools`.

## What kvm-vm does not do

kvm-vm manages VMs on one host. It does not:

- live-migrate VMs, or manage clusters or shared storage such as Ceph/RBD;
- keep more than one Snapshot per VM, or make Backups itself (it only checks them
  before a Restore);
- add more than one network card, set VLAN tags, or manage data disks (you can
  attach data disks yourself, and kvm-vm leaves them alone);
- prepare Windows guests;
- change an existing VM to match an edited definition (no drift correction).

## Development

```bash
python3 -m pytest tests -q   # offline tests; real-VM tests skip unless run as root on a KVM host
bash tests/smoke.sh
```

See [Testing](tests/README.md) for the real-host tests.

## License

GPL-3.0. See [LICENSE](LICENSE).
