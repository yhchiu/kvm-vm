# Ticket 01 validation

Fixed review point: `3c764cd2a63e3927ac7201153e357f88e6fb9149`.

The command-boundary TDD loops reproduced unsupported Macvtap configuration,
ignored overrides, missing parent checks and reinstall applying the ambient
parent. Each was observed failing before its implementation change.

Verification before review:

- Windows fast suite: 231 passed (210 existing plus 21 Macvtap cases).
- Repository smoke script: passed, including compilation, CLI help/version,
  offline definition validation, and the same 231 fast tests.
- The smoke fixture now converts its YAML key path on MSYS so native Python
  reads the actual temporary key rather than interpreting `/tmp` as a drive path.
- Debian 13 authorized test host, isolated source directory: smoke passed and
  all 231 fast tests passed. These tests simulate external virtualization
  commands; no existing VM or host networking was changed.

Real guest/LAN acceptance belongs to ticket 05 and has not been claimed here.
The fixture filesystem and mocked iproute2 responses prove orchestration and
preservation, not physical-switch compatibility or guest boot.

Review gate: pending committed Standards and Spec review.
