# External Panel security acceptance (live gate)

This is separate from the native storage-level contract in this directory.
Use a disposable actual Panel and pinned Central with two customer accounts,
two VMs on the same Host, another Host and a VM with a known transfer time.
Create recognizable positive samples for both customers and internal metrics;
verify independently with the operator query API. Preserve sanitized request,
response, authorization and time-boundary evidence. Mark PASS/FAIL/NOT RUN;
no production customer access until all applicable checks pass.

| Case | Required outcome |
|---|---|
| Positive controls | A and B see their own nonzero CPU/disk/network values; compare against operator data. |
| BOLA / IDOR | A requests B's service, guesses UUID, changes path/tenant/user/session body: no B data, no backend query. Repeat B->A, deleted/unassigned resources. |
| Fixed API contract | Raw PromQL, regex, label injection, arbitrary URL, unknown metric, metadata/export/write/admin paths rejected; no infrastructure labels returned. |
| Shared host / identical name | Two VMs distinguished by trusted host_id/vm_id; no name-only fallback; old UUID-less series not exposed. |
| Ownership history | A->B transfer: distinct known samples immediately before/after boundary; neither sees other owner's data. Verify actual engine's 300s rate + 180s guard and 60s gauge + guard, including old raw counter immediately before lookback; null at boundary. |
| Source time | Measure collector/cache/balloon ages, clock skew, delayed replay; reject unbounded/ambiguous time provenance. Managed writer spoofing tests must pass against actual pinned backend. |
| Rename, migration, rebuild | Preserve authorized history; use correct host and new UUID intervals; no duplicate totals or overlap; unknown history denied. |
| Revocation and policy outage | Revoke/change ACL while request in flight; response withheld. DB outage fails closed. No stale cache or old session authorization. |
| Cache and quotas | A->B->A same time/metric; no shared data. Concurrent user/service/global quotas enforced; oversized range/points, backend timeout and malformed responses fail safely. |
| Network/auth boundary | From real customer network, TSDB/Grafana/private query endpoint inaccessible; from Panel correct TLS/IP/credential succeeds, wrong IP/password/revoked credential denied, no TLS bypass. Writer credential cannot query; query credential cannot write. |
| API response | Empty/NaN data is null, not zero; scope mismatch/out-of-window timestamps rejected; no raw errors, source labels or secrets in response/logs. |
| Failure/recovery | Central unavailable and restored, safe user error and positive control restored; no fallback to unauthenticated queries or alternate host. |

The existing `test_panel_monitoring.py` covers planning, scope validation,
negative controls and reference callbacks with a synthetic backend.
`test_panel_query_ingress.py` exercises actual NGINX/TLS against a synthetic
upstream. They are intentionally not proof of actual TSDB query semantics,
network segmentation or the external Panel's session/ACL/quotas. Re-run live
checks after any backend, exporter, auth, or query contract change. Keep
customer-readiness BLOCKED for direct Central access regardless of this gate.
