English | [繁體中文](commands.zh-TW.md)

# Command reference

```text
kvm-vm validate <vm.yaml> [--clone]
kvm-vm create <vm.yaml> [--console-password-file <path>] [--refresh-image] [--no-start] [--dry-run]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm reinstall <vm> [--os <alias|url|path> | --image-url <url> | --image-path <path>] [--os-variant <variant>]
                      [--console-password-file <path>] [--refresh-image] [--no-start] [--dry-run] [--yes] [--force]
kvm-vm clone <source-vm> <target.yaml> [--console-password-file <path>] [--no-start] [--dry-run]
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
kvm-vm snapshot create <vm> [--no-quiesce] [--dry-run]
kvm-vm snapshot status <vm>
kvm-vm snapshot delete <vm> [--yes]
kvm-vm snapshot rollback <vm> [--yes] [--force] [--no-start]
kvm-vm backup check <backup-dir> [--until <checkpoint>]
```

`kvm-vm --help` and `kvm-vm COMMAND --help` show the same options.

- [Before you start](#before-you-start)
- [validate](#validate) · [create](#create) · [list](#list) · [status](#status) · [console](#console)
- [reinstall](#reinstall) · [clone](#clone) · [delete](#delete)
- [snapshot](#snapshot) · [backup check](#backup-check)
- [When a command refuses to run](#when-a-command-refuses-to-run)

## Before you start

Run kvm-vm as root, for example with `sudo`. It manages the system libvirt
instance (`qemu:///system`) and files under `/var/lib`. Only `validate` works
without root.

Options shared by several commands:

| Option | Commands | Effect |
|---|---|---|
| `--dry-run` | `create`, `clone`, `reinstall`, `snapshot create` | Run the checks and stop before changing anything: nothing is downloaded, no disk is changed and no VM is defined. `create`, `clone` and `reinstall` also print the effective definition. |
| `--no-start` | `create`, `clone`, `reinstall`, `snapshot rollback` | Leave the VM shut off afterwards, for example to inspect `virsh dumpxml` first. |
| `--yes` | `reinstall`, `delete`, `snapshot delete`, `snapshot rollback` | Skip the prompt that asks you to type the VM name. Required when there is no terminal, such as in scripts. |
| `--force` | `reinstall`, `delete`, `snapshot rollback` | Power off a running VM first (`virsh destroy`). Without it, a running VM is refused. |
| `--console-password-file` | `create`, `clone`, `reinstall` | Where to write a new console password. Required when the definition has `console_password: true`, and an error otherwise. See [Access and security](access.md#console-password-login). |

`create`, `clone`, `reinstall`, `delete` and the `snapshot` commands that change
something take a lock, `/var/lib/kvm-vm/state/.lifecycle-lock`, so only one runs
at a time. A command that finds the lock stops at once. If an earlier run was
interrupted, the lock stays; see
[Sequencing and interrupted operations](lifecycle-recovery-safety.md#sequencing-and-interrupted-operations)
before you remove it.

## validate

```bash
kvm-vm validate web01.yaml
kvm-vm validate --clone web02.yaml     # a clone target: no image needed
```

Reads the definition, applies defaults and [environment overrides](configuration.md#environment-overrides),
reads the SSH key files, and prints the effective definition. It does not contact
libvirt, so it cannot catch host problems such as a missing bridge; use
`create --dry-run` for that.

## create

```bash
sudo kvm-vm create web01.yaml
sudo kvm-vm create web01.yaml --dry-run
```

1. Checks the definition, that the name is free, the network attachment, and the
   SSH keys. Picks a MAC address when `mac: auto`.
2. Downloads the image if it is not cached yet (`--refresh-image` downloads it
   again; see [Image cache](configuration.md#image-cache-and---refresh-image)).
3. Copies the image to a new disk, `/var/lib/libvirt/images/vm/NAME.qcow2`, and
   grows it to `disk_gib`.
4. Writes the cloud-init seed and defines the VM with `virt-install`.
5. Saves the effective definition and the state manifest.

The command returns when the VM has started. cloud-init then sets up the guest,
which takes a minute or more. To wait for it from the guest:

```bash
ssh admin@<vm-ip> sudo cloud-init status --wait
```

If `create` fails before the VM is defined, it removes only the files it created.
Later failures keep everything for inspection, and a retry refuses to overwrite
the leftovers. If the VM no longer exists in libvirt and only its kvm-vm state
and definition are left, `create` can reuse the name. Details are in
[Lifecycle operations around recovery state](lifecycle-recovery-safety.md#sequencing-and-interrupted-operations).

## list

```bash
sudo kvm-vm list              # all libvirt VMs, fast
sudo kvm-vm list --managed    # only VMs created by kvm-vm
sudo kvm-vm list --ips        # also look up addresses (slower)
```

Columns: name, state, managed by kvm-vm, autostart, vCPUs, memory and addresses.
See [Find a VM's address](networking.md#find-a-vms-address) for where `--ips`
gets its data.

## status

```bash
sudo kvm-vm status web01
```

Shows the libvirt domain information, the kvm-vm state manifest, the Snapshot
state, disks, network cards and known addresses.

## console

```bash
sudo kvm-vm console web01
```

Attaches to the serial console. Press `Ctrl+]` to leave. Attaching does not
unlock any password; to log in on the console, see
[Console password login](access.md#console-password-login).

## reinstall

Reinstall replaces the VM's system disk with a fresh copy of a cloud image.
**Everything stored on the system disk is lost.** Data disks you attached stay
attached and untouched.

```bash
sudo kvm-vm reinstall web01                     # same OS as before
sudo kvm-vm reinstall web01 --os debian13       # switch to another OS
sudo kvm-vm reinstall web01 --os rocky9 --force --yes   # in a script, VM running
```

Kept from the saved definition: vCPUs, memory, disk size, MAC address, IP
settings, cloud-init user and SSH keys, SSH port, console password setting, and
the network attachment (bridge, NAT network or Macvtap parent). Environment
overrides are ignored.

Changed: the guest gets new SSH host keys, so SSH clients will warn about a
changed key (remove the old one with `ssh-keygen -R <vm-ip>`). If console
password login is on, a new password is required and created; the old one stops
working.

| Option | Meaning |
|---|---|
| `--os <alias\|url\|path>` | A [built-in alias](configuration.md#built-in-aliases), an image URL or a local image path. |
| `--image-url <url>` | Use this image URL. |
| `--image-path <path>` | Use this local image. |
| `--os-variant <variant>` | Set the libosinfo variant for `virt-install`. |
| `--refresh-image` | Download the image again even if it is cached. |
| `--console-password-file <path>` | Required when console password login is on. The new password is written before the VM is shut down or the disk is replaced. |
| `--no-start`, `--dry-run`, `--yes`, `--force` | See [Before you start](#before-you-start). |

Use only one of `--os`, `--image-url` and `--image-path`.

`--dry-run` checks the definition, network, SSH keys and credential path. The
Snapshot, checkpoint and disk dependency checks run only in a real reinstall.

If a reinstall is interrupted, see
[Interrupted reinstall](lifecycle-recovery-safety.md#interrupted-reinstall).

## clone

Clone copies a shut-off VM's system disk to a new VM with its own identity.

```bash
sudo virsh shutdown web01
sudo virsh domstate web01            # wait for "shut off"
kvm-vm validate --clone web02.yaml
sudo kvm-vm clone web01 web02.yaml
```

The target definition looks like
[`examples/clone-target.yaml`](../examples/clone-target.yaml). It has no
`storage.image`: the source disk is the image. If `disk_gib` is left out, the
clone keeps the source disk size.

What happens:

```text
source system disk
      |
      +-- qemu-img convert --> new independent qcow2
                                  |
                                  +-- virt-sysprep, selected operations only:
                                  |     machine-id, ssh-hostkeys, net-hostname,
                                  |     net-hwaddr, dhcp-client-state,
                                  |     udev-persistent-net, lvm-system-devices,
                                  |     random-seed
                                  |
                                  +-- cloud-init clean
                                  |
                                  +-- new MAC address and cloud-init instance ID
                                  |
                                  +-- network, user and SSH settings from the target YAML
```

kvm-vm does not run `virt-sysprep`'s default operation set, so user accounts and
application data in the guest are kept. The source VM is not changed.

The clone keeps the source's OS variant, so `virt-install` gives it the same
virtual hardware defaults. To choose another variant, for example after an
in-place upgrade of the source guest, set `vm.os_variant` in the target
definition. See [os_variant](configuration.md#os_variant). `clone --dry-run`
shows the inherited value; `validate --clone` cannot, because it does not know
the source.

Limits:

- Linux guests only (a `virt-sysprep` limit).
- The source must be shut off.
- The source system disk must be a standalone qcow2 or raw file.
- Only the system disk is copied. Data disks are not.
- The target disk cannot be smaller than the source. A larger disk is allowed;
  the guest's cloud-init or tools must grow the partition and filesystem.
- VMs with a Snapshot, checkpoints, backing chains or running jobs are refused.

For console passwords on clones, see
[Clones and reinstalls](access.md#clones-and-reinstalls).

### Check virt-sysprep support

`kvm-vm clone ... --dry-run` runs `virt-sysprep --dry-run` on the source disk
with the operations above, so that is the quickest test. kvm-vm needs at least
`machine-id` and `ssh-hostkeys`; it skips other operations that the host's
`virt-sysprep` does not have.

To test an image that is not a VM yet:

```bash
virt-sysprep --list-operations         # what this host supports
virt-inspector -a /path/to/image.qcow2 # must report the OS and its mount points
virt-sysprep --dry-run -a /path/to/image.qcow2 \
  --operations machine-id,ssh-hostkeys,net-hostname,net-hwaddr,dhcp-client-state,udev-persistent-net,lvm-system-devices,random-seed
```

If the last command finishes without mount errors or "unknown OS" warnings,
clone will work with that image.

## delete

```bash
sudo kvm-vm delete web01                # asks you to type the name
sudo kvm-vm delete web01 --yes          # no prompt
sudo kvm-vm delete web01 --force --yes  # also power off a running VM
sudo kvm-vm delete web01 --keep-storage # undefine only, keep all files
```

For a VM created by kvm-vm, `delete` removes the libvirt domain and only the files
kvm-vm recorded: the system disk, the cloud-init files, the effective definition
and the state manifest. It never uses `virsh undefine --remove-all-storage`, so
data disks you attached later are never deleted. Console password files are not
deleted either; remove them yourself.

- A running VM is refused unless you add `--force`.
- `--keep-storage` removes only the libvirt domain. kvm-vm keeps the disk,
  cloud-init files, definition, state and NVRAM, so nothing is orphaned silently.
- For a VM that kvm-vm did not create, only `--keep-storage` is allowed.

## snapshot

A Snapshot is one short-lived rollback point per VM, taken before a risky change.
It lives on the same host and storage as the VM, so it is **not a Backup**.

```bash
sudo kvm-vm snapshot create web01                  # running or shut off; no reboot
sudo kvm-vm snapshot status web01                  # none, ready or needs attention
sudo kvm-vm snapshot delete web01 --yes            # keep the change, drop the Snapshot
sudo kvm-vm snapshot rollback web01 --yes --force  # undo the change and start again
```

For a running VM, `create` briefly freezes the guest filesystems through the QEMU
guest agent. `--no-quiesce` skips the freeze and gives a crash-consistent
Snapshot. Rollback returns the disk, the VM configuration and the kvm-vm files to
the Snapshot and keeps the VM's identity.

Snapshots and incremental Backups are never combined. Read
[Snapshots](snapshots.md) before first use: it covers the tested hosts, what is
checked, failures, AppArmor hosts, and the order of steps with Backups.

## backup check

kvm-vm does not make Backups itself. The
[Backup and Restore procedure](full-backup-restore.md) uses `virtnbdbackup` and
`virtnbdrestore` directly. It passed one cross-host exercise (Debian 13 to
AlmaLinux 9.8, one guest), for a full Backup and for chosen points of a full plus
incremental history; other guests, applications and host combinations are
untested. Before any Restore, check the Backup directory:

```bash
kvm-vm backup check /backups/web01
kvm-vm backup check /backups/web01 --until virtnbdbackup.2
```

`virtnbdrestore` can exit successfully while restoring an incomplete or
out-of-order history. `backup check` reads the directory only, refuses such a
history, and prints the checkpoints a Restore would apply. See
[ADR 0004](adr/0004-pre-restore-backup-check.md).

## When a command refuses to run

`clone`, `reinstall` and `delete` work on VMs with a standalone qcow2 or raw
system disk and, optionally, file or block data disks. They refuse a VM that has:

- a kvm-vm Snapshot or an unfinished Snapshot operation (delete it first with
  `kvm-vm snapshot delete`, which keeps the current data);
- libvirt checkpoints, other snapshots or backing chains;
- disk paths that do not agree between libvirt and kvm-vm;
- running block or backup jobs.

A refusal changes nothing. The error message names what to check. The full rules
are in [Lifecycle operations around recovery state](lifecycle-recovery-safety.md).
