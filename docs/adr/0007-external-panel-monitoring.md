# ADR 0007 — External VM Panel integration

[繁體中文](0007-external-panel-monitoring.zh-TW.md)

- Status: Accepted for the integration interface; customer rollout pending the Panel's own acceptance.
- Baseline: `b3a2a1ed`.

An existing multi-user VM Panel owns authentication, service ownership, ACLs,
placement history and the UI. It is a trusted service, not an untrusted tenant.
Use a private HTTPS, authenticated, query-only service endpoint on Central and
fixed, versioned VM-scoped queries in the Panel backend. Retain VictoriaMetrics
Single, operator Grafana, all Host deployment modes and Remote Write. No new
production daemon, duplicate customer DB, mandatory registry synchronization,
or choice of a future storage-level tenant backend.

The shared query credential can read all stored series through query_range.
This is application-level isolation, not native storage-level isolation.
Customer browsers cannot possess this credential or choose PromQL, labels,
upstream URLs or tenant paths. Panel authorization must resolve trusted host/VM
IDs and historical ownership/placement intervals on every request; bound each
query including its lookback and source timestamp uncertainty. Keep unassigned
resources, infrastructure metrics and internal labels out of customer results.
Use trusted infrastructure and verify writer provenance, timestamp bounds and
actual backend behavior before rollout. If a host can forge data for another
VM, or the Panel is compromised, this design is insufficient as an independent
security boundary.

ADR 0006 still governs direct customer access to monitoring/Grafana and
storage-level tenancy; `customer-readiness` stays BLOCKED. This ADR does not
certify a third-party Panel or change that gate. Phase 5's native tenancy
contract remains intact for future backend evaluation. Panel-specific live
acceptance is a separate gate; require operator sign-off and evidence. Defer
Phase 6 backend PoCs until capacity, independent tenant isolation or per-tenant
retention/governance requires them. See [integration contract](../integration/monitoring-api.md).
