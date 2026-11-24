# Backend-independent monitoring foundation

Status: implementation scope for phases 0–2. Baseline: `866a0f7` (uploaded `kvm-vm.261119.zip`). The central backend remains VictoriaMetrics Single for administrators. Customer readiness MUST stay BLOCKED. Preserve Local, Agent and Hybrid, Remote Write protocol, existing host inventory and credentials. Do not implement customer login, tenant routing, storage abstraction or select a backend.

## Evidence baseline

The supplied `central-e2e-20261118-dbc99c8.tar.gz` contains `E2E_REPORT.md`, `results/`, `logs/`, and `docs/plans/20261118_central-hardening/acceptance.md`. It records the single-machine dual NAT lab (9/9), writer recovery, two independent Single tenant handoff, and blank-guest restore (86 seconds under prepared conditions). Real inter-site testing and production customer isolation were not established. The archive remains an external evidence artifact, not committed logs or secrets. The previous handoff and roadmap are historical; this specification and ADR 0006 govern the new scope.

## Deliverables

- Stable libvirt domain UUID (`vm_id`), including in monitor JSON and VM metrics. No inferred UUID from name. Protect against a name being reassigned during collection; skip ambiguous samples. UUID is an identifier, not authentication. Preserve metric names, CLI table and existing host-level labels. Adding a label starts new time series; old samples remain readable but are not automatically stitched. Dashboard grouping must preserve host and VM identity.
- Add libvirt disk operations and cumulative I/O time (seconds), NIC packets/errors/drops, and guest balloon timestamp/freshness. Missing or unsupported values are absent in exposition/null in JSON, never fabricated. Do not invent VM uptime from CPU time.
- Central operator-only file-backed registry: tenant slug/status, VM UUID, host placement history and time-bounded tenant ownership (unassigned = internal). A shared host may contain multiple tenants. Validate known hosts using existing inventory. Explicit operations, UTC epoch seconds, half-open intervals, strict ordering, atomic writes and writer lock. No automatic inference from host tenant metadata or incoming metrics. Retire without deleting history; never reuse a retired UUID. Disabled tenants cannot receive new assignments. Existing host-level tenant metadata is retained for compatibility, not authorization.
- No customer access: neither the registry nor the UUID label enforces read/write authorization. Future trusted admission, per-sample ownership validation, routing, queue cutover and query isolation remain required. No history rewrite or transfer on ownership change.

## Acceptance

Run offline unit and regression tests, shell syntax, patch apply/tree verification. Real libvirt/Docker/NAT tests must be rerun on the dedicated lab; do not claim previous lab results validate new code. Follow tickets in `issues/`.

Evidence archive SHA-256: `8550ea91d99cfe31bdd3cf038c206095e75046233b611d0131c10b20bdf9b188`.

## Phases 3–4 (second batch)

See [security and reliability guide](../../monitoring-security-reliability.md) for the operator-managed writer lifecycle, optional dedicated ingress, local diagnostics, central self-monitoring, independent observer and acceptance drills. Keep the legacy deployment and customer-readiness BLOCKED. Do not treat a Host UUID binding or test ingress as tenant authorization. The real storage binding drill, extended outage/overflow/status-code experiments and offsite recovery remain live acceptance gates. No backend selection or production customer access in this phase.
