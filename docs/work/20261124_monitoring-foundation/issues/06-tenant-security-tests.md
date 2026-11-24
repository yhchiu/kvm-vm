# Phase 5: Shared tenant security acceptance

- Implement a live HTTP adapter and common synthetic two-tenant contract with independent storage oracles.
- Verify positive controls, one mixed-ownership batch, spoofing, API/cache enumeration, delayed samples, policy failure and credential revocation.
- Negative controls must fail; unavailable evidence is BLOCKED. Produce a machine-readable report, never credentials.
- Keep real backend deployment, benchmark and final ADR for Phase 6/7. Keep customer access BLOCKED.
- See [fixture protocol](../../../../tests/tenancy/README.md).
