English | [繁體中文](README.zh-TW.md)

# Testing

| Test | Where it runs | Command |
|---|---|---|
| [Offline tests](#offline-tests) | Any machine, no root | `python3 -m pytest tests -q` |
| [Smoke test](#smoke-test) | Any machine with bash | `bash tests/smoke.sh` |
| [Lifecycle E2E](#lifecycle-e2e) | KVM host, root | `sudo bash tests/run_e2e.sh` |
| [Distro matrix](#distro-matrix) | KVM host, root | `sudo python3 -m pytest tests/test_distro_e2e.py -v -s` |
| [Snapshot and recovery E2E](#snapshot-and-recovery-e2e) | KVM host, root | `sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s` |
| [Macvtap and console acceptance](#macvtap-and-console-acceptance) | Separate controller plus KVM host | see the workflow documents |
| [Backup acceptance](#backup-acceptance) | Two KVM hosts | see the workflow document |

Install `pytest` first (`python3-pytest`, or `pip install pytest`).

## Offline tests

```bash
python3 -m pytest tests -q
```

This runs every test file. Tests that need a real KVM host skip themselves unless
they run as root on Linux with `/dev/kvm` and a working libvirt connection. Tests
that need POSIX file permissions skip on Windows.

> [!WARNING]
> On a KVM host, running this command as root also runs the real-VM tests, which
> create and delete VMs. To run only the offline tests there, add `-m "not e2e"`
> (or run as a normal user):
>
> ```bash
> sudo python3 -m pytest tests -q -m "not e2e"
> ```

## Smoke test

```bash
bash tests/smoke.sh
```

Compiles the scripts, runs `--help` and `--version`, runs the main offline test
files when pytest is installed, and validates the example definitions. It needs
no libvirt.

## Lifecycle E2E

`run_e2e.sh` checks the host (Linux, root, `/dev/kvm`, tools, libvirt, the
`default` NAT network) and then runs `tests/test_e2e.py`:

```bash
sudo bash tests/run_e2e.sh                          # Debian 12 guest
sudo bash tests/run_e2e.sh --distro ubuntu24.04 -v  # another alias; extra args go to pytest
```

It covers `--dry-run` checks, create with a NAT network, SSH login, hostname and
user checks, `status` and `list`, an offline clone, a custom SSH port (and its
reset to 22 on the clone, and the rollback when the new port is blocked),
reinstall with `--force --yes`, `delete --keep-storage`, and full delete with
storage cleanup.

Requirements: a Linux host with `/dev/kvm`, root, a running libvirt daemon with
the `default` NAT network, and `virsh`, `virt-install`, `qemu-img`,
`virt-sysprep`, `ssh`, `ssh-keygen` and a cloud-init seed tool.

SSH waits up to 180 seconds. Rocky Linux and AlmaLinux install SELinux tools at
first boot and can need longer on slow hosts:

```bash
sudo KVM_VM_E2E_SSH_TIMEOUT=600 bash tests/run_e2e.sh --distro rocky8 -v
```

## Distro matrix

Creates one VM per alias (2 vCPUs, 2048 MiB, 20 GiB, NAT, SSH port 2222) and checks
key login, the actual OS version and that cloud-init finished. Rocky and Alma
cases set up SELinux and any active firewalld for the port. VMs are removed even
when a case fails.

```bash
sudo python3 -m pytest tests/test_distro_e2e.py -v -s
sudo KVM_VM_E2E_DISTROS=debian13,rocky9 KVM_VM_E2E_SSH_TIMEOUT=900 \
  python3 -m pytest tests/test_distro_e2e.py -v -s
```

`KVM_VM_E2E_DISTROS` picks aliases (default: all). `KVM_VM_E2E_SSH_TIMEOUT` sets
the wait (default: 600 seconds).

The host CPU must support what the image needs: Rocky Linux and AlmaLinux 9 need
x86-64-v2, and their version 10 images need x86-64-v3. A timeout on a CPU that
is too old is a failed check, not a pass. Results so far are in
[REAL_MACHINE_RESULTS.md](REAL_MACHINE_RESULTS.md).

## Snapshot and recovery E2E

```bash
sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s
sudo python3 -m pytest tests/test_recovery_e2e.py -v
```

`test_snapshot_e2e.py` creates an `e2e-snap-*` guest (`KVM_VM_E2E_DISTRO`, default
`debian12`) and checks Snapshot create, status, delete and rollback with other
lifecycle commands. Its `SNAPSHOT_*` output lines are the measured evidence. Run
it before relying on Snapshots on a new host; see
[Tested host configurations](../docs/snapshots.md#tested-host-configurations).

The Snapshot test honors the `KVM_VM_*` path variables, so it can run beside
production VMs with separate directories.

`test_recovery_e2e.py` checks that lifecycle commands refuse checkpoints, backing
chains and dependent images without changing anything, and that `reinstall` and
`delete` work with data disks. It uses its own temporary directories and defines
VMs without starting them. It does not prove a Restore.

## Macvtap and console acceptance

These opt-in workflows drive a real KVM host from a separate controller machine:

- [MACVTAP_ACCEPTANCE.md](MACVTAP_ACCEPTANCE.md): Macvtap networking, checked from
  a client on the LAN.
- [CONSOLE_ACCEPTANCE.md](CONSOLE_ACCEPTANCE.md): console password login. The
  controller also needs Paramiko and the host needs pexpect; neither is a kvm-vm
  dependency.

## Backup acceptance

[docs/full-backup-acceptance.md](../docs/full-backup-acceptance.md) describes the
two-host Backup and Restore exercise. It uses the `prepare_*.py` and
`restore_recovery_point.py` helpers in this directory.
