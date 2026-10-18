# Lifecycle operations around recovery state

`clone`, `reinstall` and `delete` support a single regular qcow2 system disk
whose live XML, persistent XML and managed disk path agree. They refuse
checkpoints, snapshots, backing chains, persistent backup bitmaps, external data
files and unsupported disk layouts. Refusal retains the existing domain and
files; it does not consolidate chains or retire recovery metadata for you.
An unavailable inspection command or an inspection error also stops the work.
No virtnbdbackup installation is required for ordinary operations.

Before replacing or deleting storage, kvm-vm checks references from other defined
domains and qcow2 files under `KVM_VM_DISK_DIR`. Symlinks are resolved and direct
hard-link aliases are checked. This inventory is local: administrators must not
keep unregistered dependent images outside the managed disk directory, share
these disks with another host, or modify storage concurrently. Such arrangements
are outside the supported coordination model. Move or retire dependencies using
their owning tool before retrying; do not delete a backing image to silence a
refusal.

`delete --keep-storage` keeps managed disk, cloud-init, definition and state
artifacts, and requests NVRAM preservation from libvirt. It still refuses
unsupported topology and recovery metadata. Keeping files is not a complete
Backup: capture domain configuration and any additional firmware/device state
required for independent Restore.

## Checkpoints and incremental backups

Libvirt checkpoint metadata tracks changes for Backup. Removing this metadata
is not the same as deleting a historical Snapshot or deleting Backup copies.
kvm-vm refuses it instead of implicitly passing `--checkpoints-metadata` during
undefinition. Resolve the checkpoint and bitmap lifecycle explicitly with the
backup tool. A disk replacement needs a new full Backup baseline; do not resume
an old incremental chain after replacement. This ticket does not certify any
incremental recovery procedure.

The inspection interfaces are documented in the [virsh manual](https://www.libvirt.org/manpages/virsh.html#checkpoint-list)
and [QEMU image utility manual](https://www.qemu.org/docs/master/tools/qemu-img.html).
Shared read-only image inspection may see concurrent metadata changes, so it is
only a preflight check. Forced shutdown is followed by another topology check
before replacement or undefinition.

## Sequencing and interrupted operations

Create, clone, reinstall and delete share an exclusive directory lock at
`KVM_VM_STATE_DIR/.lifecycle-lock`. Use one state directory for all kvm-vm
processes controlling a libvirt instance. Commands fail immediately on an
existing lock; they do not automatically reclaim it based on an old PID. After
an interrupted process, inspect the `owner` file, processes, domain state and
storage before removing a stale lock. A different state directory or host does
not participate in this lock.

Direct virsh, qemu-img, virtnbdbackup and other external operations do not acquire
the lock. Schedule them separately from kvm-vm lifecycle commands. Finish active
backup, migration, block copy, commit or pull jobs first; do not start a new one
between preflight and completion. The domain-job and block-job checks detect
existing work, but cannot exclude an independently started external operation.

Failed create/clone operations retain surviving domain, disk and cloud-init
files for inspection. A retry refuses existing disk, conversion-temp or
cloud-init artifacts rather than overwriting them. Failed reinstall retains
surviving resources and reports whether replacement began; it cannot undo an
already completed destructive replacement. Inspect actual domain references,
then recover or explicitly remove only resources no longer needed.

## Validation

Fast regression tests use the actual command handlers and temporary files, with
host programs simulated. `tests/test_recovery_e2e.py` exercises real libvirt
checkpoint and external-chain refusal through the CLI. It compares file hashes
and persistent XML before and after rejected commands. Its small qcow2 fixture
has no guest OS; it proves refusal and preservation, not guest boot or Restore.
Run it on a disposable-capacity Linux/KVM host as root:

```sh
python3 -m pytest tests/test_recovery_e2e.py -v -s -p no:cacheprovider
```

The fixture owns only uniquely named `e2e-recovery-*` domains and matching
directories under `/var/lib/libvirt/images`; cleanup preserves files if its
domain cannot be undefined. Ordinary guest boot, clone and reinstall behavior
remains covered by `tests/run_e2e.sh`. Missing prerequisites are pending
acceptance, not passing evidence.
