# 06: Make kvm-vm executable in git

**Status:** complete

**Bug:** A clean checkout of `kvm-vm` is mode `100644`. `tests/smoke.sh` runs
`$ROOT/kvm-vm` directly, so smoke exits 126 with `Permission denied` until
someone runs `chmod +x`. Real-host regression of `217d145` hit this on both
Debian 13 and AlmaLinux 9.8.

**Decision:** Store `kvm-vm` as `100755`. `tools/kvm-vm-bridge` is already
`100755`. Shell helpers that the docs invoke with `bash` (`install.sh`,
`tests/smoke.sh`, `tests/run_e2e.sh`) stay non-executable in git.

**Tests:** `test_shipped_commands_are_executable` checks the working tree
mode. `bash tests/smoke.sh` must pass on a tree whose only extra step is
the git checkout itself.

- [x] `git update-index --chmod=+x kvm-vm`
- [x] Confirm `tools/kvm-vm-bridge` is already `100755`
- [x] Unit test
