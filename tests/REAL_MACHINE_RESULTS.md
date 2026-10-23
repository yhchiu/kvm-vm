# Distribution create and SSH verification — 2026-10-15

This run covers the eight aliases not covered by the preceding Debian 12 and
Ubuntu 24.04 lifecycle tests. It uses the same user-provided KVM host throughout.
Results describe the actual image downloaded or already cached on that host;
the aliases use upstream `latest` URLs and do not pin an image release.

Host: Debian 13, Intel Xeon L5420, KVM, default libvirt NAT. The physical CPU has
neither `sse4_2` nor `popcnt`, and has no AVX/AVX2. Guests use `host-passthrough`,
2 vCPUs, 2048 MiB RAM, and a 20 GiB disk. Test authentication uses a temporary
Ed25519 key and the `admin` account on SSH port 2222.

| Alias | Create | SSH and cloud-init | Notes |
| --- | --- | --- | --- |
| `ubuntu26.04` | Passed | Passed | Actual VERSION_ID: 26.04 |
| `debian13` | Passed | Passed after boot fix | Actual VERSION_ID: 13; see below |
| `rocky8` | Passed | Passed | Actual VERSION_ID: 8.10; SELinux Enforcing |
| `rocky9` | Passed | Failed: SSH timeout | CPU lacks required x86-64-v2 features |
| `rocky10` | Passed | Failed: SSH timeout | CPU lacks required x86-64-v3 features |
| `almalinux8` | Passed | Passed | Actual VERSION_ID: 8.10; SELinux Enforcing |
| `almalinux9` | Passed | Failed: SSH timeout | CPU lacks required x86-64-v2 features; init panics |
| `almalinux10` | Passed | Failed: SSH timeout | CPU lacks required x86-64-v3 features |

The initial matrix uses a 600-second SSH provisioning timeout. Hardware-limited
failures remain failures; they are not skipped or counted as successful SSH
verification. A newer host is required to finish verification of those aliases.

Final outcome for the eight requested aliases: four passed complete create/SSH
verification, and four created successfully but could not complete SSH on this
hardware. Debian 12 and Ubuntu 24.04 passed the preceding lifecycle tests and
were excluded from this eight-alias run.

## Debian 13 boot fix

The initial `create` returned successfully, but the guest never acquired an IP
and SSH timed out. A serial log showed GRUB's `Booting Debian GNU/Linux` message
with no subsequent kernel output. Adding only a VGA adapter to that same guest
allowed Linux and cloud-init to start. A fresh, unmodified `create` with the fix
then passed the complete SSH test in 137 seconds.

Commit `0545e5b` adds `--video vga` to `virt-install`, retaining `--graphics none`
and the serial console. No VNC or SPICE server is enabled.

The stall depends on the host. On a second host (AlmaLinux 9.8, QEMU 10.1.0,
AMD Ryzen 7 7700), an existing Debian 13.7 guest boots normally without a video
device. It uses the same `pc-q35` machine with SeaBIOS and the same
`terminal_output gfxterm serial` GRUB setting as the stalled guest. The
remaining differences are the CPU and the QEMU/SeaBIOS build. The test host's
Xeon L5420 has no EPT or unrestricted guest support, so KVM must emulate
real-mode code such as GRUB's BIOS video calls. This is the most likely cause,
but it has not been isolated. QEMU logged no KVM internal error during the stall.
[Debian bug #1111240](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=1111240)
reports the same `Booting Debian GNU/Linux` stall on a guest without a video device.

All four passing aliases were verified using the final runtime at `0545e5b`.
Ubuntu 26.04 and AlmaLinux 8 passed their final two-case run in 716 seconds.
Rocky 8 passed a fresh create in 355 seconds. An earlier Rocky 8 diagnostic run
was forcibly restarted during initialization and later reported invalid host
keys; its failure is retained in the logs. The fresh case was observed through
a read-only serial connection and completed without a diagnostic restart.

## Hardware limits

With VGA present, AlmaLinux 9 reaches `/init` and immediately reports:

```text
Kernel panic - not syncing: Attempted to kill init! exitcode=0x00007f00
```

AlmaLinux 10's VGA screen remains at its GRUB boot entry. The host's missing CPU
features independently make it unsuitable for these images. The CPU limitation
is supported by the requirements below; the console evidence alone does not
prove every cause of a stalled boot.

- [Rocky Linux 9 CPU requirements](https://docs.rockylinux.org/releases/release_notes/9_0/): x86-64-v2.
- [Rocky Linux 10 CPU requirements](https://docs.rockylinux.org/releases/release_notes/10_0/): x86-64-v3.
- [AlmaLinux 9 CPU requirement, maintainer issue response](https://bugs.almalinux.org/view.php?id=289): x86-64-v2.
- [AlmaLinux 10 architectures](https://almalinux.org/blog/2025-05-27-welcoming-almalinux-10/): default x86-64-v3, with a separate v2 build. This host does not meet v2 either.

## Reproduction

Run as root on a Linux KVM host with the repository's E2E prerequisites:

```bash
KVM_VM_E2E_DISTROS=ubuntu26.04,debian13,rocky8,rocky9,rocky10,almalinux8,almalinux9,almalinux10 \
  python3 -m pytest tests/test_distro_e2e.py -v -s -p no:cacheprovider
```

Each passing case verifies the authenticated user, hostname, OS ID and version,
`cloud-init status --wait`, and absence of a listener on port 22. Rocky/Alma cases
configure the custom port in SELinux and any active firewalld before SSH restarts.

Unit regression checks: 141 passed on Windows and 141 passed on the Linux host.
The Windows full suite also skips the 13 real-machine cases because it is not a
Linux KVM host.

## Evidence and cleanup

The evidence archive retains pytest logs and JUnit XML, diagnostic serial logs,
VM XML, boot screenshots, and a host snapshot. The final passing runs are in
`matrix-debian13-fixed.log`, `matrix-final-compatible.log`, and
`matrix-rocky8-fresh.log`. Failed attempts remain in `matrix-a.log`,
`matrix-a-resume.log`, and `matrix-b.log`.

All temporary test domains, managed disks, cloud-init seeds, state files, and
definitions were removed. Uploaded source and diagnostic scripts, generated
test private keys, and the remote workspace were cleaned up after downloading
the evidence. The original `acsvm-vm-910001` domain remained shut off.

## Follow-up on an x86-64-v2 host — 2026-10-19

`rocky9` and `almalinux9` were rerun on a host that meets x86-64-v2:
AlmaLinux 9.8, Intel Xeon E5-2650 (Sandy Bridge-EP: SSE4.2, POPCNT and AVX, no
AVX2), libvirt 11.10.0, qemu-kvm 10.1.0, Python 3.9.25, default libvirt NAT.
Guests used `host-passthrough`, 2 vCPUs, 2048 MiB RAM, a 20 GiB disk and SSH
port 2222, with the same test steps as above. The host has EPT and unrestricted
guest support, so the GRUB stall seen on the L5420 host did not need
investigation here.

| Alias | Create | SSH and cloud-init | Notes |
| --- | --- | --- | --- |
| `rocky9` | Passed | Passed | Actual VERSION_ID: 9.8; SELinux Enforcing |
| `almalinux9` | Passed | Passed | Actual VERSION_ID: 9.8; SELinux Enforcing |

The first run (images downloaded) passed in 405 s. A second run on the exact
`0afdabf` tree (LF line endings) passed in 116 s with cached images. The first
run used a CRLF copy produced by a Windows checkout, which Python tolerated; the
second run is the evidence for the commit. `rocky10` and `almalinux10` remain
unverified because this CPU lacks x86-64-v3. This is consistent with the earlier
`rocky9` and `almalinux9` failures coming from the host CPU rather than from kvm-vm.
Test domains were removed by the fixtures; pytest logs and JUnit XML are on the
host under `/root/evidence`.
