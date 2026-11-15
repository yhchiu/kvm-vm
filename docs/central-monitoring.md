# Central monitoring (Remote Write)

**Design:** `kvm-vm exporter` -> `vmagent` (or local Prometheus in hybrid mode) -> authenticated HTTPS ingress -> central VictoriaMetrics Single -> Grafana and vmalert/Alertmanager. Supports hosts behind NAT, with outbound-only connections. There is no VM agent and no dependency on libvirt on the central server.

## Deployment modes

| Mode | Host components | Historical samples | Uploads centrally |
|---|---|---|---|
| `local` (unchanged default) | Exporter + Prometheus + Grafana | Local, 15 days by default | No |
| `agent` | Exporter + vmagent | Queue only, not a queryable TSDB | Yes, outbound HTTPS |
| `hybrid` | Exporter + Prometheus + Grafana | Local, 15 days by default | Yes, outbound HTTPS |

**Only one remote writer per host:** hybrid reuses the existing local Prometheus; it never runs vmagent in parallel. The agent mode intentionally *does not* install local Grafana or Prometheus.

## 1. Install the central server

Requirements: Linux, Docker Engine, Docker Compose v2, Python 3; no libvirt or VM management program is required.

```bash
sudo ./tools/setup-central-monitoring.sh up
sudo ./tools/setup-central-monitoring.sh credentials
```

The central stack runs VictoriaMetrics Single, Grafana, vmalert and Alertmanager. Default bind addresses: `127.0.0.1:8428` (VictoriaMetrics API) and `127.0.0.1:3000` (Grafana). Neither interface is public. In particular **VictoriaMetrics has no built-in authentication in this deployment**; NEVER expose its port to the internet. The named Docker volumes survive `down`, reboot, and upgrades. Storage retention defaults to 30 days:

```bash
sudo env GRAFANA_PORT=3100 VM_PORT=8430 VM_RETENTION=60d \
    ./tools/setup-central-monitoring.sh up
```

Optional: `render DIRECTORY` previews the configuration without Docker. Subsequent `up` calls do not overwrite existing `.env`, dashboard/rules files or the hashed-credentials file. To upgrade, edit the pinned image tags in `/etc/kvm-vm/central/compose.yaml`, then run `update` after taking a backup.

### HTTPS/Basic Auth ingress (required for remote hosts)

Use a dedicated DNS name, e.g. `metrics.example.com`, resolving to the central machine. Set up a valid TLS certificate using your existing NGINX/certbot or another HTTPS reverse proxy. Inside that HTTPS virtual host, use the location blocks from:

`tools/monitoring/central/nginx-remote-write.example.conf`

The proxy accepts **only POST `/api/v1/write`**, requires Basic Auth with a **per-host credential**, and forwards to `127.0.0.1:8428`. Block all other routes from this public virtual host. Restrict ingress by source firewall rules where feasible and rate-limit requests as needed. If `VM_PORT` is customized, update the example NGINX `proxy_pass` port accordingly. Do not disable TLS verification on the agents.

Enroll a unique username for each host (use the host UUID if convenient):

```bash
sudo ./tools/setup-central-monitoring.sh enroll kvm-tpe-01
# Shows a randomly generated password ONCE and saves only a bcrypt hash.
```

The `enroll` command uses a short-lived `httpd:2.4-alpine` Docker container to run `htpasswd -B` with password supplied on stdin. On the central host, copy `/etc/kvm-vm/central/htpasswd` to `/etc/nginx/kvm-vm/htpasswd`, ensuring the NGINX worker can read it, and reload NGINX. Re-copy the file each time you enroll, rotate or revoke a credential. **Revocation:** remove the matching line from the central htpasswd, deploy it to NGINX, and reload NGINX; a disabled host's existing queued data can no longer be uploaded. Do not expose `/metrics` from the KVM hosts publicly.

Grafana should be used by internal administrators only. Access over SSH port-forwarding (`ssh -L 3000:127.0.0.1:3000 ...`) or an authenticated, TLS-protected private admin network. The provisioning does **not** implement per-customer isolation.

## 2. Install host Agent mode

Each KVM host needs Docker Compose v2, systemd, Python 3, virsh and a working libvirt service. Create a root-readable password file on the host using the *one-time* credential from central enrollment:

```bash
sudo install -d -m 0700 /etc/kvm-vm
sudo sh -c 'umask 077; cat > /etc/kvm-vm/central-password'
# Type/paste password and press Ctrl-D. Avoid putting it in shell history.

sudo env \
  KVM_VM_REMOTE_WRITE_URL=https://metrics.example.com/api/v1/write \
  KVM_VM_REMOTE_USERNAME=kvm-tpe-01 \
  KVM_VM_REMOTE_PASSWORD_FILE=/etc/kvm-vm/central-password \
  KVM_VM_MONITOR_HOST=kvm-tpe-01 \
  KVM_VM_MONITOR_SITE=taipei \
  ./tools/setup-monitoring.sh up --mode agent
```

The generated `remote.json` stores a **random, stable `host_id` UUID**, a human-friendly host, and site. This metadata labels scraped VM and Collector metrics, differentiating identical VM names on different KVM hosts. Never regenerate the ID on restart or mode transition. The host-level `remote-password` is stored in an owner-only directory (`0700`); the file is `0644` *within* that private directory so a non-root collector process inside its bind-mounted Docker container can read it. It is not inserted in Compose `.env` or printed as command-line arguments.

`vmagent` uses a Docker named volume to persist its queue, capped by default at **2 GiB per destination**. If the destination stays offline and the queue is full, oldest buffered samples are lost. This is a best-effort buffer, **not an indefinite archive**. Metrics scrape interval is 15s; exporter samples locally every 5s. Agent self-metrics are available at `http://127.0.0.1:8429/metrics`.

To inspect or stop the Agent (preserves queue):

```bash
sudo ./tools/setup-monitoring.sh status --mode agent
sudo ./tools/setup-monitoring.sh logs --mode agent
sudo ./tools/setup-monitoring.sh update --mode agent
sudo ./tools/setup-monitoring.sh down --mode agent
```

## 3. Hybrid mode: local Grafana plus central history

On the KVM host, set the same `KVM_VM_REMOTE_*` environment variables as above and run:

```bash
sudo ./tools/setup-monitoring.sh up --mode hybrid
```

This boots the **existing local** Prometheus/Grafana and overlays its Prometheus config with `remote_write`. It preserves the existing TSDB and Grafana volumes. Hybrid relies on Prometheus WAL buffering, which generally cannot recover unsent data from outages longer than about **two hours**; choose `agent` with vmagent's disk queue for longer intermittent outages. Hybrid does not forward the full local Grafana configuration, dashboards or alerts — only sampled metrics. The local scrape config in hybrid uses `external_labels` for remote host identity; the central time series always contain `host_id`, `host`, and `site`.

**Important:** The generated `prometheus-hybrid.yml` provides the standard exporter scrape job. If you have customized the original `prometheus.yml` with more scrape jobs, review and merge them into the hybrid config after the first deployment. Subsequent `up` runs preserve customized scrape files. A deliberate `KVM_VM_REMOTE_RECONFIGURE=1` overwrites generated configurations; back up custom jobs first. The original file is never overwritten. Do not run Agent and Hybrid concurrently for the same Host.

## 4. Mode changes and configuration persistence

To change from `agent` to `hybrid` (or the reverse), **stop the current mode first** and then start the other mode. `down` never includes `docker compose down -v`; queue, Prometheus TSDB and Grafana volumes remain intact. The `remote.json` UUID and `remote-password` file are reused. A mode marker prevents accidentally running the original local installer over a remote configuration.

```bash
sudo ./tools/setup-monitoring.sh down --mode agent
sudo ./tools/setup-monitoring.sh up --mode hybrid
```

To return to local-only, stop the remote mode, then run `sudo ./tools/setup-monitoring.sh up`. The localhost VM exporter may be reused if it is already running. Note: mode switching does not transfer samples between vmagent's queue and Prometheus's WAL. Wait for the queue to drain before cutting over if continuity matters.

Saved settings: `/etc/kvm-vm/monitoring/remote.json`, `remote.env`, `remote-password`, `vmagent-scrape.yml`, `prometheus-hybrid.yml`, and per-mode Compose files. Saved identity and secret files are not overwritten on subsequent runs. To **change** a remote endpoint or username, explicitly edit `remote.json`, review the change, and re-run the mode's `up` with `KVM_VM_REMOTE_RECONFIGURE=1` (overwrites generated scrape files). To rotate a password, update `remote-password` only after registering a new hash at the ingress and validate both before revoking the old credential. Avoid changing the vmagent remote URL unnecessarily because it can change how on-disk queues are associated with endpoints.

## 5. Central Dashboard and alerts

The central Grafana dashboards are automatically provisioned:

- **KVM Fleet Overview:** hosts with recent metrics, running VM count, VM CPU, assigned memory, disk and network activity by Host.
- **KVM Fleet – Virtual Machines:** Site/Host/VM filters, VM CPU/memory/I/O and collector status. Different Hosts can have VMs with the same names; do not group by `vm` alone.

`vmalert` evaluates rules for collector failures and sustained growth of the Agent remote-write queue. **Alertmanager is deliberately installed without a notification receiver**: no email/webhook is sent until an operator configures a receiver in `/etc/kvm-vm/central/alertmanager/alertmanager.yml`. Central lost-host alerts require an explicit persistent inventory of **expected** hosts; without it, a completely offline Host produces no new metrics and cannot reliably be inferred from `up == 0`. This additional inventory and automated credential lifecycle are a follow-on production hardening phase, not part of this deployment.

## 6. Operational checks

From each KVM Host:

```bash
curl -fsS http://127.0.0.1:9179/metrics | grep kvm_vm_collector_success
curl -fsS http://127.0.0.1:8429/metrics | head  # Agent only
sudo ./tools/setup-monitoring.sh status --mode agent
```

From the central host:

```bash
curl -fsS http://127.0.0.1:8428/health
sudo ./tools/setup-central-monitoring.sh status
sudo ./tools/setup-central-monitoring.sh logs
```

A queue-growth alert can only reach the Central server while data is flowing; it cannot observe a completely disconnected Host in real time. A future persistent Host inventory is necessary for reliable offline-host alerts.

At the Central query API verify `kvm_vm_collector_success{host_id="..."}` exists after the first successful upload. If the Host is NATed, no inbound port is necessary; only outbound HTTPS to the ingress is required.

## Limitations and deployment validation

The scripts and YAML can be tested without Docker/virsh; a real KVM host and public TLS ingress are required to verify actual scraping, ingest, Grafana panels and alert delivery. A single VictoriaMetrics instance is **not high availability**: back up its volumes and test restore. Restrict retention and queue storage budgets, monitor disk usage, and avoid leaving the Central API publicly accessible. The central endpoint remains intentionally inaccessible from remote hosts **until you finish the authenticated HTTPS ingress setup**.
