# Isolated Central Monitoring KVM/NAT Lab

[繁體中文](README.zh-TW.md)

This is a **destructive-to-lab-only, opt-in integration lab**. It uses a *single disposable Linux KVM hypervisor* to emulate two independently NATed sites and an Internet-facing central-monitoring server. The actual `kvm-vm` monitoring stack is installed inside the lab VMs; the Python offline tests do **not** substitute for a successful live run.

## Topology

```text
                        Physical KVM host (management controller)
                      /       |                           \
         isolated site A      | WAN (libvirt NAT)         isolated site B
            10.10.1/24        | 172.30.0/24               10.10.2/24
                |             |                               |
  KVM Host A 10.10.1.10      Central .10          KVM Host B 10.10.2.10
  Exporter + vmagent       VictoriaMetrics      Exporter + Prometheus
  Agent mode              Grafana, vmalert     Hybrid mode
                |          NGINX HTTPS :443                    |
      NAT Router A           |                       NAT Router B
      LAN 10.10.1.2         WAN                    LAN 10.10.2.2
      WAN 172.30.0.11        |                     WAN 172.30.0.12
               \             |                              /
                +----------- WAN --------------------------+
```

- Three libvirt networks: `kvm-e2e-cm-wan` (**NAT**, `172.30.0.0/24`), `kvm-e2e-cm-site-a` and `kvm-e2e-cm-site-b` (**isolated**, `10.10.1.0/24` / `10.10.2.0/24`). No changes to libvirt's `default` network.
- Five disposable VMs: Central, two Linux NAT routers, two simulated nested KVM Hosts. The site routers NAT all outbound traffic; the outer libvirt WAN also NATs outbound to the physical host's upstream connection.
- Only the physical hypervisor has out-of-band access to all three bridges for SSH provisioning. Central's only NIC is in WAN; an additional **central-guest-only blackhole route** for each private LAN prevents the privileged physical hypervisor from accidentally providing site ingress. The live connectivity scenario must also prove it cannot connect to either private site's SSH directly. If that test fails, **treat the isolation as broken**, do not claim a valid NAT lab.
- Central is reachable at `172.30.0.10:443` with a **short-lived private lab CA** and separate per-Host Basic Auth identities. Neither Agent nor Hybrid disables TLS verification.

## Prerequisites

- **Disposable physical host**, Linux, `qemu:///system`, `/dev/kvm`; root privileges. Budget **at least 24 GiB host RAM**, ~80 GiB of free disk space, and enough CPU for five nested workloads. Guest settings total ~17.5 GiB RAM before any nested test VMs.
- Installed on physical host: `virsh`, `virt-install`, `qemu-img`, `cloud-localds`, `ssh`, `ssh-keygen`, `openssl`, `python3`, `iproute2`.
- A **trusted local, apt-based x86_64 Debian 13 or Ubuntu 24.04 cloud image** (`qcow2`, cloud-init and systemd included). The script does **not** silently download an unverified image. Package repos must be reachable from the virtual WAN (through the physical host's Internet connection).
- Guest provisioning requires `apt-get`, `docker.io`, a Docker Compose v2 apt package (`docker-compose-v2` or `docker-compose-plugin`), `nginx`, `iptables`, `libvirt` and appropriate CPU flags. Confirm guest images/repository package names in the environment to be tested.
- For **running nested same-name VMs**: expose nested virtualization to the first-level Host VMs (`/dev/kvm` inside Host A/B). If unavailable, that scenario **fails explicitly**; other Remote Write/network scenarios can still run.
- The lab uses fixed private subnets; it fails preflight when an existing physical route overlaps. **Do not run multiple instances on one physical host** with the same fixed subnets. Run on a machine without production workloads where possible.

## Commands

From repository root:

```bash
# Read-only preview: no root, libvirt or Docker required
./tests/e2e/lab.sh plan

# Full one-command creation + VM bootstrap (installs packages inside lab guests)
sudo ./tests/e2e/lab.sh up --image /path/to/debian13-cloud.qcow2

# If you need to troubleshoot the guest boot/provisioning separately:
sudo ./tests/e2e/lab.sh up --image /path/to/debian13-cloud.qcow2 --no-bootstrap
sudo ./tests/e2e/lab.sh bootstrap

sudo ./tests/e2e/lab.sh status
sudo ./tests/e2e/lab.sh test                 # fast smoke: connectivity, metrics, auth
sudo ./tests/e2e/lab.sh test all             # full suite, including ~10+ min offline alert
sudo ./tests/e2e/lab.sh test same-name-vms
sudo ./tests/e2e/lab.sh test disconnect-recover

# Manually impair only one disposable Router VM
sudo ./tests/e2e/lab.sh fault host-a disconnect
sudo ./tests/e2e/lab.sh heal host-a
sudo ./tests/e2e/lab.sh fault host-b netem --latency 150 --loss 3 --rate 1mbit
sudo ./tests/e2e/lab.sh heal host-b

# Stops and undefines only manifest-owned lab VMs/networks;
# keeps report, CA, credentials and deployment diagnostic state by default.
sudo ./tests/e2e/lab.sh down
sudo ./tests/e2e/lab.sh down --purge    # ONLY if lab is still initialized; delete state too
```

Use `sudo ./tests/e2e/lab.sh --lab-id cm ...` for the default Lab ID (`cm`). Paths and IDs are restricted, and `up` refuses to adopt preexisting libvirt domains or networks. Lab state is stored under `/var/lib/kvm-vm-e2e-lab/cm/` with root-only permissions, including test credentials and the CA private key. **Never commit or upload this state directory.** You can choose a dedicated volume via `KVM_VM_E2E_STATE_ROOT`.

**Important:** The VM disks are *copy-on-write overlays* over the supplied base cloud image. Do not delete or modify the image while the lab is running. `down` deletes lab-created overlays; it does not delete your base image or any Docker volumes outside the disposable VMs.

## Automated scenarios

| Scenario | What must be observed |
|---|---|
| `connectivity` | All five SSH endpoints reachable by controller; TLS validation succeeds; Central cannot directly SSH to site Hosts |
| `double-nat` | Each Host routes via its dedicated Router MASQUERADE, then through libvirt WAN NAT |
| `metrics` | Both Host UUIDs appear at Central for `kvm_vm_collector_success` and `node_memory_MemAvailable_bytes` |
| `same-name-vms` | Each nested KVM Host runs a QEMU VM named `e2e-web01`; Central sees two separate `host_id` series |
| `latency-loss` | 150 ms delay, 1% loss, 1 Mbit/s on Router WAN; recent metrics still arrive; `tc` rule is removed |
| `disconnect-recover` | Router blocks Remote Write; vmagent's disk queue grows; vmagent is restarted; historical samples from outage are replayed after restoration |
| `auth` | Unauthenticated and incorrect Basic Auth `POST` rejected with HTTP 401 |
| `host-offline` | Inventory-based `KvmVmHostOffline` fires after sustained disconnection (up to 13 min wait), then resolves |
| `mode-switch` | Host B switches Hybrid → Agent → Hybrid without changing `host_id`; final metrics still arrive |

Each `test` produces a dated JSON **PASS/FAIL** report under `.../reports/`. Failures are reported with nonzero exit; do not treat partial passes as complete success. `disconnect-recover`, `latency-loss` and `host-offline` use `finally` blocks to heal their simulated router fault. If interrupted with SIGKILL or power loss, manually run `heal`.

## Known limitations / next work

1. **No real-hardware result included.** This environment may lack `/dev/kvm`, libvirt, Docker and public apt access; offline tests prove only scripts and generated settings, *not* running VMs or successful networking. Record actual run logs before claiming E2E passed.
2. Router connectivity depends on guest cloud-init NIC matching and distro packages. If the cloud image uses different network tooling or Docker Compose apt package names, fix the Lab installer for that distro with a test before declaring support.
3. WAN NAT and traffic shaping simulate many network failures but do not reproduce real ISP routing, independent power failures, DNS propagation or external CA lifecycle. Real cross-site validation is still needed before production.
4. The lab CA lasts seven days. Credentials and private keys live only in local lab state; rotating/revoking production credentials is a separate follow-on task.
5. This first lab automates ingress authentication, metrics, queue replay, offline alerts and mode switching. Delivery of **Email/Webhook messages**, extensive queue overflow tests, real VM guest OS workload benchmarks and operational backup/restore need dedicated follow-up scenarios; do not imply that they are already covered.
6. The test VM `e2e-web01` is a **real running QEMU domain with no boot disk**. It is sufficient to test the libvirt domain identity/metrics path, but **not** the behavior of a fully booted guest OS; add a cloud-image-backed nested domain for resource/workload tests.
7. Faults are applied inside disposable routers, never to physical host iptables/nftables. `down` is scoped by an on-disk ownership manifest and should be tested on a disposable physical host before using near customer VMs.

## Offline checks (safe on any development machine)

```bash
python3 -m pytest tests/test_monitoring_e2e_lab.py -q
bash -n tests/e2e/lab.sh
python3 -m py_compile tests/e2e/lab.py tests/e2e/inject_lab_ca.py
```

See [Central Monitoring Agent Handoff](../../docs/CENTRAL_MONITORING_AGENT_HANDOFF.md) for remaining production hardening work.
