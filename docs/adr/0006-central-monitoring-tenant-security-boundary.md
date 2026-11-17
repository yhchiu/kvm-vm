# ADR 0006 — Prepare a strict tenant boundary for customer monitoring

- **Status:** Accepted for architecture; customer-facing access is **blocked** pending implementation and verification.
- **Decision date:** 2026-11-16
- **Scope:** Central monitoring for Hosting operators now; future customer login.

## Decision

Keep the already implemented Linux KVM deployment modes (`local`, `agent`,
`hybrid`) and outbound HTTPS Remote Write. Continue using the current
VictoriaMetrics **Single** central stack **only for trusted administrators**.
Do not expose its shared Grafana datasource or query API to Hosting customers.

Every expected Host in central inventory may have an operator-controlled,
non-secret `tenant_id` slug. Legacy inventory entries implicitly belong to
`internal`. Tenant ownership is **metadata**, not a proof of identity or an
access-control policy. This information is stored only in central inventory
and exported as `kvm_vm_host_tenant_info` for administrative auditing. It is
**not** injected into VM metrics or used to authorize Grafana queries.

The `customer-readiness` preflight exits nonzero while the stack uses an
unpartitioned Single backend or an operator-wide Grafana datasource.

## Trust boundaries / threats

- **Untrusted host metrics:** An authenticated Remote Write sender can send
  arbitrary label sets, including another `host_id` or `tenant_id`. HTTP Basic
  Auth proves possession of a credential, not that a claimed metric label is
  correct. Do not join host-provided labels to inventory and expose the
  resulting series to customers without source validation.
- **Dashboard filters are presentation:** Grafana `host`/`site`/`vm` variables,
  folders and panels do not block a user from querying a shared datasource.
- **Admin vs customer:** Administrative querying may intentionally span all
  hosts/tenants. Customers must have a separate query identity with enforced
  tenant scoping *below Grafana*, including API and ad-hoc queries.
- **Inventory vs credentials:** `inventory disable/remove` does not revoke a
  Remote Write credential. Credential revocation must occur at the ingress
  before any customer-facing access can be enabled.
- **Do not trust client-chosen paths:** The Remote Write destination path,
  username or `tenant_id` field must not determine a customer's effective
  storage partition without server-side policy.

## Customer access implementation gate (future phase)

1. Implement **native tenant separation** in the storage/query layer, e.g.
   VictoriaMetrics Cluster with tenant-specific `vminsert`/`vmselect` APIs,
   or strictly separate per-tenant backends. For this project we prefer the
   cluster path when customer-facing access becomes a committed requirement.
2. Bind each per-host credential to exactly one centrally registered `host_id`
   and `tenant_id`. At an authenticated ingress, select the tenant-specific
   write destination based on the **server-side credential mapping**. Reject
   caller-supplied tenant selection and enforce host identity using an
   appropriate trusted relabeling / validation mechanism (or place each host
   in its own authenticated write pipeline). Do not rely on the received
   `host_id` label for admission.
3. Provision **tenant-scoped query credentials** and query API routing. Each
   customer Grafana datasource must be incapable of reading any other tenant,
   even with crafted PromQL, direct HTTP API calls, label enumeration,
   arbitrary time ranges or dashboard imports. Disable direct access to the
   raw VictoriaMetrics API and privileged multi-tenant endpoints.
4. Keep admin-only Grafana separate from customer identities. Choose
   per-tenant Grafana organizations/instances or a customer portal with a
   brokered datasource; enforce query tenancy in the datasource proxy/backend
   regardless of UI/role configuration.
5. Before enablement, run two-tenant negative tests for spoofed `host_id`,
   changed `tenant_id`, wrong credentials, cross-tenant series search,
   alert-query enumeration, API bypass, query cache, token expiration and
   credential revocation. Include ingress and data retention migration tests.
6. Change `customer-readiness` from fail-closed **only after** all layers
   above are implemented, the test evidence is recorded and an operator
   enables customer access explicitly.

**No customer login or tenant-scoped data access is implemented by this ADR.**

## Why not use single-node labels as authorization?

Single-node VictoriaMetrics does not provide native multitenancy. Its query
label filters can be useful for internal views but alone are insufficient to
prove isolation: clients can spoof labels on ingestion, and unauthorized query
paths can bypass UI filters. Cluster-native tenant paths provide a stronger
storage-level boundary. A cluster is not necessary for the initial internal
admin-only MVP, so we defer the added operational complexity until the
customer-facing phase is tested end-to-end.

## Compatibility

- Legacy inventory file format remains accepted without migration.
- `kvm_vm_host_expected{host_id,host,site}` keeps its old labels, so existing
  alert expressions and dashboards remain valid.
- Host collection/Remote Write configuration and `local`/`agent`/`hybrid`
  mode semantics are unchanged in this milestone.
- `tenant_id` is an operator metadata field, not a required Host-side secret.
