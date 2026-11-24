# Tenant security contract (Phase 5)

This is a backend-neutral **acceptance harness**, not a tenant gateway. The
current shared VictoriaMetrics Single deployment remains admin-only and
`customer-readiness` remains BLOCKED. No new production services are installed.
The old `tests/e2e/tenant_handoff.py` and `managed_writer_drill.py` remain intact:
they test host-level handoff and writer credentials, not VM-level tenancy.

## Run the contract self-tests now

```sh
python3 -m pytest tests/test_tenant_security_contract.py -q
```

These test the harness using deliberately defective models and transport/format
checks. A passing model is not real backend evidence. No Docker or root needed.

## Live candidate test (future native tenancy)

Run only against an isolated, disposable fixture. A real candidate needs an
authenticated write gateway, a tenant-scoped read gateway, independent operator
storage oracles for A, B and internal, and a fixture controller. A raw backend
alone cannot satisfy VM-level routing or authenticate a customer. Provisioning
actual candidates and their tenant-aware ingress is Phase 6, not implemented by
this harness. Existing shared Single is **not** a valid customer backend.

```sh
cp tests/tenancy/fixture.example.json /tmp/tenant-fixture.json
chmod 600 /tmp/tenant-fixture.json
# Set real disposable endpoints, credentials, controller and attack routes.
# Set disposable=true only after verifying isolation from production.
python3 tests/tenancy/run.py --fixture /tmp/tenant-fixture.json \
  --allow-disposable-fixture --timeout 30 --quiet 10 \
  --report /tmp/tenant-contract-result.json
```

Exit 0 = every case passed, 1 = a security/contract assertion failed, 2 =
blocked/incomplete (including preparation, transport, cleanup errors). After
any failure dependent scenarios are NOT_RUN. Report is created exclusively;
choose a new filename per run. The report lists synthetic fixture IDs, time
boundaries, probe timestamps/values, durations, and bounded observation window, not passwords or URLs.
Keep exact gateway config revision, image tags/digests, topology, sanitized
logs and real access-control evidence separately with the report.

### Adapter and fixture protocol

The shared `Contract` calls `control(action, fixture)`, `write(principal,
samples, attack=None)`, `read(principal, endpoint, params, attack=None)`,
`observe(scope, run_id, sample)`, and `attacks()`. It uses real sample values
and timestamps. `HTTPAdapter` implements these through actual HTTP requests
(Prometheus Remote Write 1.0 Snappy/protobuf, Prometheus-compatible JSON read
APIs). It does not implement authorization. Alternative adapters may implement
the same interface, but must query actual storage and preserve the exact
principal identity. Do not substitute a simulated oracle for live runs.

The private (0600) fixture JSON declares `backend` (`isolated-single`,
`vm-cluster`, `mimir`, or `shared-single-negative-control`), `disposable:true`,
`ca_file` (optional if using system trust), private `credentials_file`,
`control_command` (absolute executable and optional argv), and five base URLs.
`write` and `read` must use HTTPS; operator oracles may use loopback HTTP.
No redirects, environment proxies, invalid TLS, or URL-embedded credentials.
The base URL may contain a prefix; attack paths start at the same origin root.
All normal paths are appended to the base, e.g. `/api/v1/query`.

Credentials JSON is a dictionary with the following keys: `host1`, `host2`,
`reader-a`, `reader-b`, `wrong`, `oracle-a`, `oracle-b`, `oracle-internal`.
After rotation, `host1-old` must retain the original host1 credential.
Each value is either `{"basic":["username","password"]}` or
`{"bearer":"token"}`. Use distinct credentials. The `wrong` credential
must be invalid for both planes. Never commit this file. Oracle identities
must be privileged to observe their respective storage namespace independently
of the customer gateway and must not filter using untrusted tenant labels.
The oracle API must expose raw range-vector samples for the test marker; its
cache must be disabled (the client requests `nocache=1`). The three oracles
must remain reachable while policy admission is suspended. On Mimir use a
trusted operator proxy injecting the fixed tenant header; on Cluster use a
fixed account/project path; on independent Single use the actual separate DBs.

The control executable receives one JSON object on stdin per invocation:
`{"action":"prepare", "fixture":{...}}`. Exit zero only when the requested
operation is effective. No shell interpolation; stdout/stderr are discarded
by the harness to avoid leaking credentials. Keep private controller logs
locally if required. Control has a 120s timeout per action. The fixture supplies
unique run ID, tenant slugs, Host and VM UUIDs, and integer UTC `start` and
`transfer` (ownership changes use half-open intervals, in seconds). Times are
intentionally historical, so the candidate must accept recent backfill.

| Action | Required effect |
|---|---|
| prepare | Provision unique synthetic tenants A/B, internal namespace, distinct host1/host2 writer and A/B reader credentials. VM A and B share host1, VM other belongs to A on host2, VM internal is unassigned on host1, VM move belongs to A on host1; unknown UUID is not registered. All VM names equal `same-name`. Set initial ownership from `start`. Provision credentials/oracles before returning. |
| transfer | Change VM move to B at exactly `transfer`, leaving other VMs unchanged. Preserve A's pre-transfer samples and B's post-transfer samples. Do not rewrite history. |
| policy-down | Disable the trusted ownership/admission authority (including fallback), without affecting independent oracle access. Do not just stop the client or write network. |
| policy-up | Restore authority and wait for admission to be operational. |
| rotate-writer | Rotate host1 credential and atomically update the credentials file: original credential under `host1-old`, new credential under `host1`. Old credential must be invalid. |
| revoke | Revoke host1's active writer credential; do not change the client's credentials file to a different token. Host2 remains active. |
| revoke-reader | Revoke reader A, keep B working; do not replace the test credential. |
| cleanup | Remove only the run's synthetic resources and restore any fault injection, even after partial prepare. Never delete production volumes. |

Use three real isolated partitions with no unrelated data. Keep the normal
client URLs stable throughout the run. If fixture endpoints are dynamically
created, provide fixed local proxy routes before `prepare` or update the
configuration externally before invoking the runner. Controller may update
credentials file atomically during prepare. No fixed real customer IDs.

### Attack probes

Configure at least a path and a header probe for each plane, and a query
parameter probe for writing. Target *actual victim B* and privileged paths on
the candidate; do not use only nonexistent endpoints. `fixture.example.json`
is an example for Cluster with fixture mapping B=1002 (choose isolated account
IDs). For Mimir use `X-Scope-OrgID` for B and `A|B` tenant federation, and direct
backend/admin paths. For separate Single target alternate backend paths and
forwarded tenant-selection headers. Additional probes can be added to the JSON
arrays; `path`, `headers`, and `params` may be combined. Attack probes cannot
replace credentials, origin, or the test query. Test all real bypass paths,
including direct storage network reachability from an untrusted client, in the
candidate-specific deployment test. This harness exercises the configured
public origins, not arbitrary network routes. Do not pass backend secrets to
customer credentials.

## Contract matrix

| Scenario | Acceptance |
|---|---|
| mixed-host-batch | One real write request contains A/B/internal VM + host-only samples. Each sample exists once in its intended partition only, with correct bound Host and VM identity. Independent host2 control. |
| read-isolation-cache-enumeration | Same instant/range/series/label requests A→B→A; positive own sample, no other tenant or internal samples. Arbitrary PromQL union, label names and VM UUID enumeration. |
| spoof-and-unknown | Forged native tenant/tenant label stripped or replaced, host spoof normalized or dropped, wrong host/unknown VM rejected or dropped, invalid credential denied. |
| api-bypass-and-role-separation | Path/header/query-param attacks, invalid reader, writers cannot query, readers cannot write. Accepted writes must remain in A only. |
| ownership-transfer-delayed-sample | Before/boundary/after ownership, late old sample either stays in A or is explicitly dropped, never lands in B. Both customers retain only their own historical interval. |
| policy-outage-fail-closed | No admission when trusted ownership is unavailable; no leaked/late sample after restore. Positive recovery control. This deliberately requires fail-closed/drop or rejection, not an unverified cached policy. |
| writer-rotation | Superseded writer credential rejected; replacement credential writes to correct partition. |
| credential-revocation | Revoked host cannot write, active host2 still can. |
| reader-revocation | Revoked A cannot query, B still can. |
| final-storage-recheck | Recheck all accepted/forbidden probes in every storage scope over a bounded quiet period. |

An asynchronous gateway may drop forbidden samples with HTTP 2xx; the status
alone is not evidence. Explicit auth failures require 401/403. All positive
writes are checked by raw sample timestamp/value; a broken backend dropping all
writes or returning empty customer queries fails. Samples sent earlier than
transfer are intentionally replayed after transfer. This tests timestamp-based
routing policy, **not** actual persistent queue replay; retain the existing
resilience and writer handoff drills for that. Negative observations are finite:
set quiet/timeout based on the measured pipeline/queue bounds, drain async
pipelines before completion, and repeat under delayed ingestion. A PASS is
necessary, not sufficient for readiness. Grafana datasource/org isolation,
network reachability, token expiration, real queue handoff, backups,
resource limits, extended outage and large-scale load still require live
candidate-specific acceptance. Do not turn `customer-readiness` on based on
this report alone.

For the selected external Panel path, follow [Panel live acceptance](panel-acceptance.md) instead. Native backend evaluation is deferred per [ADR 0007](../../docs/adr/0007-external-panel-monitoring.md).
