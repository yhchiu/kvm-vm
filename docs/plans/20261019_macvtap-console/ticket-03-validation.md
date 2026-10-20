# Ticket 03 validation

Fixed review point: `a92b670`. Implementation: `6cbd4fc`.
Review refinements: `b8d9c61`.

The first clone policy matrix failed for the three newly supported combinations;
the existing disabled-to-disabled path passed. Implementation replaces the
temporary clone refusal while retaining the reinstall refusal.

Linux repository smoke passed with 265 tests. Coverage includes all four policy
combinations, independent target delivery, offline inherited-password locking,
different source/target usernames, previews, existing destinations, failed
offline customization, late domain failure, source preservation and no plaintext
in managed artifacts or command arguments. External virtualization commands are
substituted; actual POSIX delivery and OpenSSL hashing run on Debian 13.

Offline locking happens only on the cloned disk, before domain definition.
Real guest password replacement and old-password rejection remain ticket 06.

## Standards review

No blocking findings. Removed the advisory obsolete single-case parametrization
and unreachable clone branches from the temporary reinstall refusal test.

## Spec review

No must-fix findings. Added the suggested offline customization failure case,
proving source preservation, no target definition and retained target disk and
credential through the existing failure handler.

Both axes passed the single focused follow-up review with no unresolved must-fix findings. The ticket gate is closed.
