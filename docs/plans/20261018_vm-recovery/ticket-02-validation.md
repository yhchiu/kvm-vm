# Ticket 02 validation

Date: 2026-10-18. Review base:
`b6fbded40eeca629d97469fc1531bbed745a7cfc`.

## Scope

The implementation conservatively refuses unsupported recovery state instead
of changing checkpoint metadata or flattening disk chains. See the operator
[procedure](../../lifecycle-recovery-safety.md) for supported topology, local
inventory limits, manual recovery and external-tool sequencing.

One implementation commit covers the guards, command-boundary regressions,
Linux/KVM refusal acceptance and documentation. Commit the passing work before
parallel Standards/Spec review; commit any required review fixes separately.
Do not start ticket 03 before this gate is closed.

## Fast verification

The regressions execute real handlers, artifact checks, locking and cleanup with
temporary files; subprocesses and tool discovery simulate external programs.
They cover checkpoint refusal across clone/reinstall/delete, stale disk paths,
backing files, internal snapshots, bitmaps, active domain/block jobs, lock
conflicts, unregistered dependent images within the managed directory, NVRAM
preservation, failed clone resources, existing cloud-init artifacts, absent
manifests and other-domain references to relocated cloud-init directories.
Ticket 01's original-data regressions remain in the suite.

Each new refusal/data-loss regression failed before its implementation change
and passed afterward. Existing orchestration tests now supply read-only host
inspection responses; the new cases retain the actual orchestration.

Final local code: 194 unit tests and `tests/smoke.sh` passed on the test host
(Python 3.13.5, pytest 8.3.5). Earlier Windows checks used CPython 3.11.9 and
pytest 9.1.1; Debian WSL smoke also passed during development. These results
alone do not establish hypervisor behavior.

## Linux/KVM environment

The operator supplied a Debian 13 test host and explicitly authorized upload of
the executable, tests and examples into isolated temporary directories.

- Kernel: `6.12.101+deb13-amd64`; x86_64 KVM.
- CPU: two Intel Xeon L5420 processors, eight cores total.
- Libvirt: 11.3.0; QEMU/qemu-img: 10.0.13.
- virt-install: 5.0.0; virt-sysprep: 1.52.3.
- Default libvirt NAT network active; `cloud-localds` available.
- Existing non-test VM remained shut off and was not operated on.

`tests/test_recovery_e2e.py` initially passed both real acceptance cases in
11.34 seconds. Each fixture creates a uniquely named KVM domain with one 1 GiB
qcow2 image and a known data pattern. The checkpoint case starts QEMU, creates
a real libvirt checkpoint, confirms forced reinstall refuses without stopping
the VM, then checks offline clone/reinstall/delete/keep-storage refusal. The
external-chain case creates a real overlay, updates persistent XML and verifies
the same commands refuse the untracked active-path change. File hashes and
persistent domain XML remain unchanged after every rejected operation.

These fixtures have no guest OS. They verify refusal and resource preservation,
not guest boot, application consistency, Backup integrity or cross-host Restore.
Fixtures only remove their own uniquely named resources and retain files if
domain undefinition fails.

On 2026-10-19 the final-code refusal rerun passed both cases in 12.15 seconds.
The tested executable matches the local executable, SHA-256
`4d21fc95685b63c572b0a85bada5314f555c627c5b206af5c84f24f4b37176dc`.
No `e2e-recovery-*` domains remained afterward. Other host domains were not
operated on.

The existing Debian 12 lifecycle suite passed all four tests in 708.67 seconds:
dry-run, keep-storage, full create/SSH/clone/reinstall/delete, and SSH-port
rollback. This run used the preceding implementation snapshot. The subsequent
changes use the inspected disk when a manifest is absent and include relocated
cloud directories in dependency checks; both have dedicated unit regressions,
and the final version passed smoke and the real refusal rerun above.

Verification is complete. The implementation still requires the committed-code
Standards and Spec review gate before ticket 03 begins.

## Review correction

Initial Standards review found no blocking violations; its advisory request to
rename cross-command tests to `test_lifecycle_...` was applied. Spec review found
one must-fix gap: the managed-directory inventory filtered `*.qcow2`, missing
dependent qcow2 images named `.img` or without a suffix.

Four command-boundary cases reproduced that gap before correction. The scan
now inspects every regular file regardless of filename. After correction, all
199 unit tests and smoke passed on the test host, and all four real KVM refusal
tests passed in 21.15 seconds, including both alternate filenames. File hashes
and persistent domain XML remained intact after rejected delete/reinstall.
The correction awaits the single focused follow-up review.
