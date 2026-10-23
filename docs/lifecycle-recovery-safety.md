# Lifecycle operations around recovery state

`clone`, `reinstall` and `delete` support an independent regular qcow2 or raw
system disk, plus administrator-attached file or block data disks. The live
and persistent disk layouts and managed system disk path must agree. Clone
copies only the system disk; reinstall replaces it with qcow2 and reattaches
the data disks before starting the VM. Delete removes only tracked managed
files and leaves data disks untouched. These commands refuse
checkpoints, snapshots, backing chains, persistent backup bitmaps, external data
files and unsupported disk layouts. Refusal retains the existing domain and
files; it does not consolidate chains or retire recovery metadata for you.
A kvm-vm [Snapshot](snapshots.md), or an incomplete Snapshot operation, is
refused the same way; `kvm-vm snapshot delete` removes it and keeps the current
data. An unavailable inspection command or an inspection error also stops the work.
No virtnbdbackup installation is required for ordinary operations.

Before replacing or deleting storage, kvm-vm checks references from other defined
domains and all regular files under `KVM_VM_DISK_DIR`, regardless of filename
suffix. QEMU inspects image metadata to discover dependencies. Symlinks are resolved and direct
hard-link aliases are checked. This inventory is local: administrators must not
keep unregistered dependent images outside the managed disk directory, share
these disks with another host, or modify storage concurrently. Such arrangements
are outside the supported coordination model. Move or retire dependencies using
their owning tool before retrying; do not delete a backing image to silence a
refusal.

`delete --keep-storage` keeps managed disk, cloud-init, definition and state
artifacts, and requests NVRAM preservation from libvirt. It also permits a
block-backed system disk because it does not mutate storage. It still checks
all disks for recovery metadata and active jobs; network-backed disks remain
unsupported. Keeping files is not a complete
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

Create, clone, reinstall, delete and the mutating `snapshot` commands share an exclusive directory lock at
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

Before attempting domain definition, failed create/clone operations remove
only the disk successfully created by that attempt and its exclusively reserved
cloud-init directory. Cleanup requires a successful domain inventory, no domain
or image references to those resources, and unchanged file identities. An
inspection or cleanup failure retains resources and reports manual recovery
without replacing the original error. Once definition has been attempted, or
on Ctrl-C, resources are retained because libvirt or a child tool may still
be using them. Delivered console credentials are always retained; retry with
a new credential path. Previous state and definition files are never removed
by this cleanup.

A retry refuses surviving disk, conversion-temp or cloud-init artifacts rather
than overwriting them. Failed reinstall retains
surviving resources and reports whether replacement began; it cannot undo an
already completed destructive replacement. Inspect actual domain references,
then recover or explicitly remove only resources no longer needed.

Create and clone permit a stale target managed state record when a successful libvirt inventory
confirms the domain is absent and neither current nor recorded disk/cloud-init
paths (including disk conversion temporary files) survive. Existing definition
files alone do not block creation. The old state remains during dry-run and
failed attempts and is replaced only after the new domain is created.

## Interrupted reinstall

Before undefining the VM, reinstall saves its persistent domain XML and a
`RECOVERY.txt` note in `.NAME-reinstall-*/` beside the system disk. Once
undefinition has been attempted, any failure (including Ctrl-C or SIGTERM)
keeps that directory and reports its location. A successful reinstall removes
it. The directory is private to the administrator and may contain a prepared
replacement disk; do not delete it before inspecting the failure.

1. Confirm the original command and its child tools have stopped. Inspect
   `virsh -c qemu:///system dominfo NAME`, the entire recovery note (later
   entries supersede earlier phases), and actual files.
2. If the note says disk replacement has **not** started, the original disk and
   cloud-init files have not been replaced. If the domain is absent, inspect
   `domain.xml`, then use `virsh -c qemu:///system define /path/to/domain.xml`
   to put its definition back. Verify disk paths before starting it or retrying.
3. If the note says replacement **may** have started, the XML is a reference,
   not an automatic recovery recipe. Inspect the disk, seed, effective YAML and
   state manifest together. Do not start the original XML against a partially
   reinstalled guest. Reconcile them manually, or Restore from a Backup if the
   old guest is required; reinstall cannot undo disk replacement.
4. Only after recovery, remove the retained staging directory and any stale
   lifecycle lock following the checks above. A later reinstall uses a new
   directory and does not consume or erase the previous attempt.

SIGKILL and power loss cannot print guidance; inspect these same files and
the lifecycle lock before continuing. A missing or incomplete recovery note
must be treated as an unknown phase, not proof that replacement did not start.
After power loss, verify the actual files regardless of the recorded phase.

## Validation

See the [review follow-up acceptance](plans/20261018_vm-recovery/review-followup-validation.md)
for strict-umask real-host lifecycle results and their limits.

`tests/test_reinstall_signals.py` sends real SIGINT and SIGTERM signals to the
reinstall process and its process group at preparation, undefinition and disk
replacement boundaries. It uses simulated host commands and a real blocking
subprocess, so it validates signal handling and retained recovery files without
claiming real-libvirt acceptance.

E2E lifecycle commands run in owned process groups. Cancellation and command
timeouts stop those groups before teardown. A remaining lifecycle lock blocks
automatic cleanup and reports its path; inspect the owner, domain and storage
before removing it. File cleanup requires a successful libvirt inventory that
confirms the test domain is absent. Cleanup errors are reported rather than
silently ignored.

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
