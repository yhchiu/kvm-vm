# `kvm-vm backup check` validation

Status: implemented and validated on 2026-10-20 on two hosts; review gate closed.

Implementation/review fixed point: `79bc474`.

The operator decided to turn the pre-restore chain check from ticket 04 into a
supported command. The decision and its scope are recorded in
[ADR 0002](../../adr/0002-pre-restore-backup-check.md).

## Change

- `kvm-vm backup check DIR [--until virtnbdbackup.N]` runs the same checks
  that `tests/recovery_checks.py` ran, now inside kvm-vm (`BackupChainError`,
  `check_backup_chain`). One case was added: checkpoint XML that does not
  parse is refused instead of raising an error. On success it prints the
  checkpoints a Restore applies and reminds the operator to run
  `sha256sum -c` and `virtnbdrestore -o verify`. A refusal exits 1 with
  `Do not restore from DIR: <reason>`.
- `tests/recovery_checks.py` now only imports that code for the real-machine
  harnesses, so there is one implementation.
- The Backup procedure, both READMEs and the command list use the new command.

## Tests

- `tests/test_backup_check.py` (renamed from `test_recovery_checks.py`): 21
  tests. They cover the earlier refusal cases, invalid checkpoint XML, a
  missing directory, the command output and exit codes through `kvm-vm`'s
  entry point, and a run with no external commands available.
- Workstation suite: **278 passed, 20 skipped**.

## Real machines

AlmaLinux 9.8, virtnbdbackup 2.47: a fresh guest was backed up as full plus
two incrementals (`virtnbdbackup.0` to `.2`).

| Case | Result |
| --- | --- |
| Complete set | Exit 0, `virtnbdbackup.0, .1, .2` |
| `--until virtnbdbackup.1` | Exit 0, `.0, .1` |
| `--until virtnbdbackup.5` | Exit 1, unknown recovery point |
| Copy with increment 1 touched (SHA256SUMS ok, `virtnbdrestore verify` exit 0) | Exit 1, out of modification-time order |
| Copy without increment 1, `--until .2` / `--until .0` | Exit 1 (missing file) / exit 0 |
| Run as `nobody` with `PATH=/usr/bin:/bin` on a readable copy | Exit 0 |

Debian 13, virtnbdbackup 2.28: `tests/prepare_snapshot_backup_interop.py`
passed again; it now calls the kvm-vm code through `tests/recovery_checks.py`.
`kvm-vm backup check --until virtnbdbackup.1` returned exit 0 for both of its
sets (old history and new history). On a copy whose full data file was made
the newest, SHA256SUMS and `verify` still passed and the command exited 1,
naming the order. A first attempt that touched the only increment did not
change the order and correctly passed.

Both hosts were left without test guests, sets or logs; the Debian
production guests were not touched.

## Review gate

Review of `cb82fcf...79bc474`.

- Standards: Conventional Commit with a detailed body and no co-author
  trailer. The command follows the existing CLI style (`KVMError`, a
  subcommand group like `snapshot`); it is read-only, so it takes no lifecycle
  lock. The harness import keeps one implementation. Links, the procedure's
  shell blocks (`bash -n`) and `git diff --check` pass.
- Spec (ADR 0002): only the virtnbdbackup layout tested with 2.28 and 2.47 is
  accepted, and other layouts are refused (for example a `-l copy` set without
  a checkpoint list). The command changes nothing and runs without external
  commands (unit test) or root (real run). Its output keeps checksums and
  `verify` as required steps. The procedure and READMEs call it before every
  `virtnbdrestore`.
- No must-fix findings. Advisory: the command needs kvm-vm's usual Python
  dependency (PyYAML), like every other kvm-vm command.

This gate used one inline review pass instead of separate review agents.
