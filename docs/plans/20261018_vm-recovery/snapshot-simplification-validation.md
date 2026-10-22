# Snapshot simplification validation

Status: implemented and validated on 2026-10-20 on two hosts; review gate closed.

Implementation/review fixed point: `a736b62`.

The operator chose to simplify the Snapshot lifecycle after a code review of
the whole batch (`ec1845f...e3843df`). The decision is recorded in
[ADR 0005](../../adr/0005-snapshot-failures-stop-for-the-operator.md):
kvm-vm no longer continues a failed or interrupted Snapshot operation by itself.

## Review findings this change addresses

| Finding | Verified before the change | Now |
| --- | --- | --- |
| Spec (c)1: a live delete whose commit fails part-way is reported as unchanged and kept ready | Confirmed on AlmaLinux 9.8 (disk full during commit). The base differed from its copy, and a following rollback booted with a file written after the Snapshot | The record stays `deleting` and rollback is refused. After space is freed and the VM is shut down, an offline delete keeps the current data (checksum verified) |
| New: Ctrl-C during the thaw left the guest frozen while kvm-vm said "Guest filesystems were thawed" | Confirmed in 8 of 14 attempts. The SIGINT sent to the process group killed `virsh domfsthaw` | SIGINT and SIGTERM are ignored from the freeze until the record is saved, and virsh inherits that. The thaw message is printed only after a successful thaw |
| Spec (c)3: Ctrl-C during creation reported as "No Snapshot was created" | Not reproduced (20 attempts) | A creation failure removes the record only after verifying that nothing changed. A killed kvm-vm leaves `creating`; `snapshot delete` then merges the Snapshot or removes the record |
| Spec (c)4: an interrupted record removal blocks every command | Confirmed in a command-boundary reproduction | The record is retired with one rename. An unreadable record is removed by `snapshot delete` once nothing of the Snapshot is in use |
| Standards: "Restore" used for rollback | Code, help text, README, issue 06 | Renamed to "put back" / "return to"; `restore_snapshot_artifacts` is now `reinstate_snapshot_artifacts` |
| Standards: repeated topology and `snapshot-list` checks | Seven and six places | `on_overlay`, `on_base` and `libvirt_snapshots` helpers |
| Spec (a)4: status list missing `rolling-back` | docs/snapshots.md | Listed under "needs attention" |

Removed: `settle_failed_creation`, the delete branches that continued
`metadata` and `cleanup` work, the retry path of an interrupted rollback,
resetting a failed live delete to `ready`, and the unused `live.xml` capture.
Added: the signal handling, the shared next-step texts, and the
"only the record is left" path of `snapshot delete`. The Snapshot section of
`kvm-vm` is 594 lines, compared with 598 before. The change removes guessing
paths, not size.

Not addressed here: Spec (c)2 (Ctrl-C during `reinstall` after undefinition,
ticket 01 code), and the review's other Standards and Spec items. They remain
in the review notes.

## Tests

- `tests/test_snapshot.py`: 62 tests. New or changed:
  - Ctrl-C and SIGTERM are ignored from the freeze to the final check.
  - A killed kvm-vm before or after libvirt acted.
  - A failed live commit that wrote part of the base cannot be rolled back.
  - Interrupted live and offline deletes.
  - An interrupted rollback finished with the documented manual steps.
  - A failed redefine.
  - The record removal cannot leave part of the record.
  - An unreadable record is removed once nothing is left.
- Workstation suite: **282 passed, 20 skipped**. The same unit tests passed on
  both hosts (282 passed, Python 3.9.25 and 3.13.5).

## Real machines

| Run | AlmaLinux 9.8 (libvirt 11.10) | Debian 13 (libvirt 11.3, AppArmor) |
| --- | --- | --- |
| `tests/test_snapshot_e2e.py` (create/delete and rollback) | 2 passed in 255 s; freeze 105 ms; rollback to SSH 21.5 s; delete after restart worked online | 2 passed in 695 s; freeze 848 ms; rollback to SSH 42.8 s; delete after restart refused online with rollback disabled, then deleted offline |
| SIGINT to the process group during `snapshot-create-as` (9 delays, 0-100 ms) | Guest thawed every time. Seven runs completed. Two later signals interrupted the step after the thaw (exit 130, record `creating`), which led to protecting that step too | not run |
| Live delete with the commit out of space | Refused with rollback disabled; base already changed; rollback refused; offline delete after growing the disk directory kept the data | not run |

These runs used the code before the last change, which keeps SIGINT and
SIGTERM ignored until the record is ready. The confirmation with the
committed code is in the review gate.

## Review gate

Review of `e3843df...a736b62` against ADR 0005 and the review findings.

- Standards: Conventional Commit with a detailed body and no co-author
  trailer. The rollback wording follows the glossary. The shared helpers
  replace the repeated topology and snapshot-list checks. Links, anchors and
  `git diff --check` pass.
- Spec (ADR 0005): no branch continues a stopped operation. Rollback accepts
  only a ready Snapshot. A merge starts only from a verified overlay-on-base
  state. A failed delete disables rollback. Only an empty Snapshot lets its
  record go. The freeze section cannot be interrupted, and the record is
  retired in one step.
- Edge cases checked by reading the code:
  - A Snapshot deleted or reverted with virsh: delete removes the record in
    the first case and refuses in the second.
  - An unreadable record: never merged.
  - A failure while capturing files: the record is removed only after
    checking that nothing changed.
  - Every error path puts the signal handlers back.
- No must-fix findings. Advisory: an interrupted rollback now needs the manual
  steps in docs/snapshots.md, which is the accepted cost of ADR 0005.

Confirmation with the committed code (`kvm-vm` SHA-256 `78f425b9…`, checked on
each host):

| | AlmaLinux 9.8 | Debian 13 |
| --- | --- | --- |
| Unit tests (three files, `-m "not e2e"`) | 282 passed | 282 passed |
| `tests/test_snapshot_e2e.py` | 2 passed in 249 s; freeze 104 ms; rollback to SSH 20.7 s | 2 passed in 742 s; freeze 1031 ms; rollback to SSH 54.1 s; delete after restart refused online, then offline |
| SIGINT during `snapshot-create-as` (9 delays, 0-100 ms) | All 9 completed with exit 0, record ready, guest thawed | not run |
| Live delete with the commit out of space | Exit 1, record `deleting`, base changed, rollback refused; offline delete after growing the disk directory kept the data (checksum OK) | not run |

Both hosts were left without test guests, loop filesystems or logs. This gate
used one inline review pass instead of separate review agents.
