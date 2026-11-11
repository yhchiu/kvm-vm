---
status: complete
---

# Host bridge tool and bridge preflight fixes

Real-host functional testing of kvm-vm 1.3.0 (`a0c9adb`) on Debian 13 and
AlmaLinux 9.8 found one optional tool to add and four product issues. B-003
(illegal `systemd-run` flags, and a watchdog that blocked until the timeout)
is already fixed in the tool that was live-tested. This plan covers adding
that tool and the remaining fixes.

## Decisions

- kvm-vm still does not create or change host bridges. `kvm-vm-bridge` is an
  optional root tool installed beside it.
- B-005 warns and does not refuse. A FORWARD drop does not break host SSH to
  a guest on the same bridge, and refusing would block a host that the
  operator is about to fix. `validate` stays offline. The warning runs from
  `create`, `clone` and `reinstall`, including `--dry-run`, because those
  commands already inspect the bridge on the host.
- B-006: the kernel ORs `net.bridge.bridge-nf-call-iptables` with the
  per-bridge `nf_call_iptables` bit. A per-bridge 0 does not disable
  filtering, and it is not a documented workaround.
- B-001 keeps the lifecycle lock for `--dry-run`. A user who cannot create
  the lock gets `ERROR:` and exit code 1, with no traceback.
- B-002 checks `disk_gib` against a base image that is already on disk.
  `--dry-run` still does not download an image in order to measure it.
  `clone --dry-run` checks the source disk the same way. When create or clone
  fails before any file from that attempt exists, the message says so and
  does not claim that files were retained.
- Auto-rollback archives its own session with `archive.sh`. It does not call
  `kvm-vm-bridge rollback`, which would stop the watchdog unit that is
  running the script.
- No version bump and no CHANGELOG. The repository has no changelog, and
  1.3.0 was not bumped for the previous feature commit.
- No new ADR. The boundary "kvm-vm does not change host networking" is
  unchanged and is stated in the networking docs. Glossary entries cover
  Uplink and Auto-rollback.

## Tests

Unit tests for the tool and for each kvm-vm fix. No new real-VM E2E. The
bridge tool's live dry-run is skipped unless `KVM_VM_BRIDGE_LIVE=1`.
Auto-rollback on a real uplink stays a manual check. After each task, run
`python3 -m pytest tests -q -m "not e2e"` and `bash tests/smoke.sh`.

## Commits

1. `feat: add kvm-vm-bridge to create a host bridge with auto-rollback`
2. `test: cover kvm-vm-bridge parsing, backend detection, and rollback scripts`
3. `docs: explain how to use kvm-vm-bridge safely`
4. `fix: report a clear error when the lifecycle lock cannot be created`
5. `fix: check disk size on dry-run and do not claim missing files were kept`
6. `fix: warn when the host firewall will drop bridged guest IPv4`
