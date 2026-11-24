# Monitoring documentation consolidation

Reviewed against `830f471` (Identity CLI, on top of the external Panel integration). Documentation-only change; no runtime, defaults, schema or acceptance gates changed. The current navigation is [Monitoring](../../monitoring.md) / [監控架構與文件導覽](../../monitoring.zh-TW.md).

## Inventory and disposition

| Existing material (English and zh-TW where present) | Disposition |
|---|---|
| Root README | Replace scattered trailing monitoring notes and removed roadmap links with one Monitoring section before documentation/safety/license. |
| `docs/commands` | Keep live monitor/exporter semantics; add missing identity synopsis and command entry. |
| `docs/monitoring-stack` | Keep local setup/configuration/ports; add local topology and dashboard upgrade procedure. |
| `docs/prometheus` | Merge base and additional metric references; clarify Host vs VM labels, operator examples, units and migration compatibility. |
| `docs/central-monitoring` | Reorder by installation, ingress, Host deployment, inventory, alerts, mode changes, upgrade and verification. Remove obsolete statements that inventory/writer management are future work. Link to canonical security/registry guides rather than restating their policies. |
| `docs/monitoring-security-reliability` | Retain credential lifecycle, rotation, queue diagnostics, observer and DR; move executable extended drill instructions to the Lab guide; use stopped-writer render and review before starting. |
| `docs/monitoring-registry` | Distinguish Host inventory, optional VM metadata and Panel authority. Keep temporal record/CLI constraints. |
| `docs/integration/identity`, `monitoring-api` | Clarify discovery vs authorization, IDs, trust boundary, request flow, API limits and source-time gap. Keep versioned JSON catalog and reference module as executable contract. |
| `docs/adr/0006`, `0007` | Preserve decisions; clarify direct Central/native isolation vs Panel application isolation. Add Chinese ADR 0007. |
| `tests/README`, `tests/e2e/README` | Keep test prerequisites/topology/base scenarios; centralize extended drills and distinguish historic results from current validation. |
| `tests/tenancy/README`, `panel-acceptance.md` | Keep separate native tenancy and Panel live acceptance paths, add identity acceptance and cross-links. |
| `docs/plans/20261118_central-hardening/README` | Preserve historical Host-level handoff/restore protocol; label as historical and link current commands. |
| `docs/work/20261124_monitoring-foundation/spec` and `issues/` | Preserve original implementation scope/evidence hash and tickets; add navigation/current Identity status to spec. Individual tickets are historical requirements, not live operator instructions. |
| `docs/plans/20261018_vm-recovery/spec` and general lifecycle/testing docs | Incidental monitoring references only; not monitoring setup/contract. Leave unrelated VM recovery requirements and evidence unchanged. |

## New canonical navigation

- `docs/monitoring{,.zh-TW}.md`: architecture, mode selection, ownership/trust boundaries, document index.
- `docs/monitoring-acceptance{,.zh-TW}.md`: test selection, release gates, version-specific historical evidence and pending live verification.
- `docs/adr/0007-external-panel-monitoring.zh-TW.md`: translation of existing decision, not a new architecture choice.

Source checks: CLI and monitoring sampler in `kvm-vm`; host/central setup scripts and generated templates; inventory/ownership/writer modules; query catalog and reference client; NGINX examples; alert rules, Compose and dashboard JSON; Lab and tenancy harnesses. Do not infer live validation merely from code or documentation. The prior historical evidence archive remains external to Git.

All diagrams are editable Mermaid embedded in Markdown, with adjacent prose/tables for readers without a Mermaid renderer. Existing guide paths are retained. Operational docs should be the maintained source; plans and ticket files record decisions and work history.
