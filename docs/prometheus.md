# Prometheus exporter

[繁體中文](prometheus.zh-TW.md) · [Monitoring architecture](monitoring.md)

`kvm-vm exporter` exposes **read-only** libvirt VM statistics in Prometheus
text exposition format 0.0.4. It reuses the existing `monitor` metrics model
and only needs the project's current Python dependencies and `virsh`. No agent
inside VMs, extra Python package, database, or automatically installed daemon
is required.

## Quick start

```sh
sudo kvm-vm exporter --managed
curl -fsS http://127.0.0.1:9179/metrics
curl -fsS http://127.0.0.1:9179/healthz
```

Use `--managed` to export only VMs with a `kvm-vm` state manifest; omit it to
export **all** libvirt domains. An optional positional VM name restricts output
further. The endpoint uses a cached snapshot updated in the background,
independently of Prometheus scrapes; scraping itself never invokes `virsh`.

```text
kvm-vm exporter [VM] [--managed] [--listen IPV4] [--port PORT]
                [--interval SECONDS] [--timeout SECONDS]
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--listen` | `127.0.0.1` | Numeric IPv4 bind address (local only by default) |
| `--port` | `9179` | TCP HTTP port |
| `--interval` | `5` | Background collection period, 0.5–3600 seconds |
| `--timeout` | `10` | Total timeout for all `virsh` calls in one sample, 0.1–120 seconds |
| `--managed` | off | Include only VMs with a kvm-vm manifest |
| `VM` | all | Optional single domain name |

`/metrics` always responds with HTTP 200 and includes
`kvm_vm_collector_success` (1/0), so a failed libvirt query is visible without
turning Prometheus's HTTP `up` metric to 0. On failure, VM series are **not**
served stale; the collector retries automatically. `/healthz` returns HTTP 503
when the last collection failed, and HTTP 200 when it succeeded. The most
recent successful sample time is available as
`kvm_vm_collector_last_success_timestamp_seconds`.

## Metric reference

Suffix groups such as `{read,write,flush}` denote separate metric names, not a single series name.

All VM series include `vm` (display name) and `vm_id` (libvirt domain UUID). Disk and network series also use `disk`
and `interface` labels respectively. Series are omitted when a value is
unavailable; no guessed zero is emitted.

The raw exporter does not assign monitoring `host_id`, `host` or `site`; the stock remote-mode scraper/writer adds those labels. `identity` reads their persisted configuration. Local-only queries may not have a Host UUID. See [Identity contract](integration/identity.md).

| Metric | Type | Meaning |
| --- | --- | --- |
| `kvm_vm_collector_success` | gauge | Last collection worked (1) or failed (0) |
| `kvm_vm_collector_duration_seconds` | gauge | Last collection latency |
| `kvm_vm_collector_last_success_timestamp_seconds` | gauge | Timestamp of last successful sample, zero if none |
| `kvm_vm_domain_state{vm,vm_id,state}` | gauge | Single active observed state with value 1 |
| `kvm_vm_domain_vcpus` | gauge | Assigned virtual CPUs |
| `kvm_vm_domain_cpu_time_seconds_total` | counter | Raw cumulative CPU time across vCPUs |
| `kvm_vm_domain_cpu_used_cores` | gauge | Sampled usage (1 = one host core) |
| `kvm_vm_domain_cpu_usage_ratio` | gauge | Sampled usage as share of VM vCPU capacity (1 = 100%) |
| `kvm_vm_domain_memory_assigned_bytes` | gauge | Balloon-assigned memory |
| `kvm_vm_domain_memory_guest_used_bytes` | gauge | Fresh guest-estimated non-reclaimable memory |
| `kvm_vm_domain_memory_rss_bytes` | gauge | Host-side QEMU RSS (not guest used RAM) |
| `kvm_vm_domain_disk_capacity_bytes{vm,vm_id,disk}` | gauge | Disk-source logical size |
| `kvm_vm_domain_disk_physical_bytes{vm,vm_id,disk}` | gauge | Disk-source physical size; not total backing-chain size |
| `kvm_vm_domain_disk_read_bytes_total{vm,vm_id,disk}` | counter | Raw cumulative disk read bytes |
| `kvm_vm_domain_disk_write_bytes_total{vm,vm_id,disk}` | counter | Raw cumulative disk write bytes |
| `kvm_vm_domain_network_receive_bytes_total{vm,vm_id,interface}` | counter | Raw bytes received **by guest** |
| `kvm_vm_domain_network_transmit_bytes_total{vm,vm_id,interface}` | counter | Raw bytes sent **by guest** |
| `kvm_vm_domain_disk_{read,write,flush}_operations_total` | counter | requests (`rd/wr/fl.reqs`) |
| `kvm_vm_domain_disk_{read,write,flush}_time_seconds_total` | counter | seconds (`rd/wr/fl.times` converted from ns) |
| `kvm_vm_domain_network_{receive,transmit}_packets_total` | counter | packets (`rx/tx.pkts`) |
| `kvm_vm_domain_network_{receive,transmit}_errors_total` | counter | errors (`rx/tx.errs`) |
| `kvm_vm_domain_network_{receive,transmit}_dropped_packets_total` | counter | packets (`rx/tx.drop`) |
| `kvm_vm_domain_memory_guest_stats_timestamp_seconds` | gauge | last valid balloon update timestamp |
| `kvm_vm_domain_memory_guest_stats_fresh` | gauge | 1 when usable guest memory stats are fresh, 0 otherwise, absent when inactive |

For guest-used RAM, `balloon.last-update`, `balloon.available` and
`balloon.usable` must be present and recent. See [monitor](commands.md#monitor)
for memory collection limitations. Unavailable and shut-off VM counters are
omitted (not output as zero). An initial successful collection exposes counters,
but the two-sample CPU usage gauges need a second successful collection. A
counter reset on a guest restart is handled by Prometheus `rate()`; no separate
counter persistence is introduced in the exporter.

## Prometheus scrape configuration (operator)

Merge [examples/monitor/prometheus-kvm-vm.yml](../examples/monitor/prometheus-kvm-vm.yml)
into your Prometheus config. When Prometheus runs on the **same KVM host**,
`127.0.0.1:9179` works. If Prometheus is on another machine or in a container,
`127.0.0.1` refers to that other network namespace, **not** this KVM host:
configure a secured/private network route (for example Tailscale) and bind to
that address with `--listen`. The exporter does **not** provide authentication
or TLS. Never expose its HTTP port publicly: domain and device names are
monitoring data. Limit access with firewall rules and prefer a private network.

Example operator PromQL (not customer authorization templates; use the [versioned Panel query contract](integration/monitoring-api.md) for that integration):

```promql
# CPU usage as a fraction of allocated VM vCPU capacity
rate(kvm_vm_domain_cpu_time_seconds_total[5m]) / kvm_vm_domain_vcpus

# Disk throughput in bytes/s, summed across drives per VM
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_disk_read_bytes_total[5m]))
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_disk_write_bytes_total[5m]))

# Network throughput in bytes/s, summed across interfaces per VM
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_network_receive_bytes_total[5m]))
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_network_transmit_bytes_total[5m]))

# Libvirt collector failed or has stopped updating (adapt freshness threshold
# to --interval and --timeout; default example assumes ~5s + 10s)
kvm_vm_collector_success == 0
(time() - kvm_vm_collector_last_success_timestamp_seconds) > 30
```

## Optional systemd deployment

`install.sh` **does not** install, start or enable a daemon. If continuous
monitoring is desired, use [the optional unit](../examples/monitor/kvm-vm-exporter.service):

```sh
sudo cp examples/monitor/kvm-vm-exporter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kvm-vm-exporter
sudo systemctl status kvm-vm-exporter
```

The example runs as root because local libvirt and the managed-VM state
manifests may not be readable by a regular account. Prefer a dedicated
restricted identity with working libvirt read-only permissions, if your host
supports that configuration. The unit uses systemd hardening but **does not**
make a root process fully isolated. Do not expose the exporter to untrusted
clients. Use `sudo systemctl disable --now kvm-vm-exporter` to stop it.

The exporter reads current libvirt statistics only. Historical data, alerts, retention and graphing belong to the monitoring stack (local Prometheus/Grafana or Central VictoriaMetrics/vmalert/Grafana). Host CPU, RAM and disk
monitoring belong to a separate host collector, such as node_exporter.

## VM identity and upgrade

The collector uses `virsh list --all --uuid --name` before and after the bulk
`domstats` read (one shared timeout). Ambiguous domains are skipped; a failed
listing fails collection rather than guessing a UUID. This check is not an
authentication boundary or an atomic libvirt snapshot. UUIDs survive rename,
restart and migration only if libvirt preserves them. Do not reuse UUIDs for
unrelated guests. `monitor --json` includes `vm_id`; the table is unchanged.

Existing metric names stay unchanged, but adding `vm_id` creates new series.
Older series do not acquire UUIDs retroactively; `vm_id=~".*"` matches legacy
and new series, whereas `vm_id=~".+"` matches only UUID-bearing series.
At upgrade/rename/migration, old and new series may coexist within a rate or
lookback window. Do not use those windows for billing or assume automatic
continuity/deduplication. Discover identities with [`kvm-vm identity --json`](integration/identity.md). Group central queries by `host_id,vm_id,vm` (and
optionally display labels). For existing central dashboards review
`sync-templates` then `reload`; for local dashboards back up and manually
replace the saved dashboard JSON, then restart Grafana. Upgrade the host CLI
and restart the exporter service; verify the new label before using it.

## IOPS and mean I/O request time

```promql
sum by (host_id,vm_id,vm) (rate(kvm_vm_domain_disk_read_operations_total[5m]))
rate(kvm_vm_domain_disk_read_time_seconds_total[5m]) / (rate(kvm_vm_domain_disk_read_operations_total[5m]) > 0)
```

The second expression estimates mean elapsed time per completed read from
libvirt counters; not host device utilization or a latency percentile.
Zero-operation intervals have no result. New JSON device fields retain raw
libvirt units (time in ns); aggregate read/write operations per second are
also available. VM uptime is deferred (CPU time is not uptime).
