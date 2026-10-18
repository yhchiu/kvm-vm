# Ticket 01 validation

Date: 2026-10-18. Implementation review base:
`05653cb51ee0325f9b36c76a1b33d4b53efaddde`.

## Behavior and limits

Reinstall retains existing VM resources when domain undefinition fails. Each
attempt exclusively creates its staging directory beside the disk, so neither
legacy staging files nor another attempt's files are cleanup candidates. Disk
replacement uses rename without first unlinking the original. After successful
undefinition, later failure retains surviving disk/cloud-init/management files
and asks the operator to inspect and reconcile them. It cannot undo a completed
destructive replacement. Staging cleanup failure reports the retained path and
does not replace an already active operation error.

The tests invoke `cmd_reinstall`, real YAML/state handling, real image-path
orchestration and actual cleanup over temporary marker files. Host programs are
simulated at `subprocess.run`; tool discovery is simulated at `shutil.which`.
One test injects a filesystem cleanup permission error. No libvirt or QEMU
behavior is proven by these tests, and marker files are not bootable VM images.

Red/green evidence: checkpoint and arbitrary undefine refusal initially deleted
original cloud-init files; legacy staging files were initially deleted; late
failure initially lacked recovery guidance; cleanup failure initially masked
the original error. Each regression failed before its corresponding fix and
passed afterward. The success regression also exercises real state persistence.

## Verification

- Windows CPython 3.11.9, pytest 9.1.1, PyYAML 6.0.3: the complete unit suite
  passed with 174 tests before the final success-path regression was added;
  the resulting reinstall selection passed with 18 tests.
- Debian WSL2, kernel `6.6.114.1-microsoft-standard-WSL2`, Python 3.13.5,
  pytest 9.1.1, PyYAML 6.0.3: all 175 unit tests passed.
- `tests/smoke.sh` passed (`smoke: OK`), including compilation, CLI help/version and offline
  definition validation. Its obsolete example key substitution was updated to
  match the examples' current `file:~/.ssh/id_ed25519.pub` path.
- `git diff --check` passed.

WSL dependencies were installed into ignored `.venv/wsl-packages`; run using
`PYTHONPATH="$PWD/.venv/wsl-packages" PYTEST_ADDOPTS='-p no:cacheprovider' bash tests/smoke.sh`.
The executable uses LF line endings for Linux execution.

Real checkpoint, disk-chain, guest boot and cross-host recovery checks remain
the responsibility of later tickets. WSL currently has no `virsh`; these results
do not close their real-machine gates.
