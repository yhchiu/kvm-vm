# 02: Clear error when the lifecycle lock cannot be created

**Status:** complete

**Bug:** B-001. Non-root `create --dry-run` raises an uncaught
`PermissionError` on `/var/lib/kvm-vm/state/.lifecycle-lock`.

**Decision:** Keep taking the lock for `--dry-run`. Catch `PermissionError`
and raise `KVMError` so `main` prints `ERROR:` and exits 1, with no
traceback.

**Tests:** Unit test that a denied `mkdir` becomes `KVMError`, and that
`main` prints `ERROR:` without a traceback.

- [x] Catch `PermissionError` around lock creation
- [x] Document that `--dry-run` still takes the lock
- [x] Unit test
