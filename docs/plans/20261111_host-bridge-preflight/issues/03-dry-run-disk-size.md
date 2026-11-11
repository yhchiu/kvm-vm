# 03: Dry-run disk size check and the empty-failure message

**Status:** complete

**Bug:** B-002. `create --dry-run` does not compare `disk_gib` with the base
image virtual size, so it can report that preflight passed. The real create
then rejects the size and also says surviving files were retained when none
exist. The same retained wording is printed for a clone that fails in
`make_disk_from_clone` before any new file exists.

**Decision:** When the base image is already on disk, `create --dry-run` and
`reinstall --dry-run` run the same virtual-size comparison as
`make_disk_from_base`. They still do not download an image to measure it.
`clone --dry-run` compares `disk_gib` with the source disk. If create or
clone fails before any file from that attempt exists, say that nothing was
retained.

**Tests:** Dry-run rejects a 1 GiB disk against a cached image whose
virtual size is 3 GiB. Dry-run passes the size check when the cache file is
absent. A size failure does not log "retained for manual recovery".

- [x] Share the virtual-size comparison
- [x] Call it from create, reinstall and clone dry-run
- [x] Fix the empty-failure message
- [x] Document the dry-run limit (no download)
- [x] Unit tests
