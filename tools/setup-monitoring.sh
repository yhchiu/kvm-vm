#!/usr/bin/env bash
# Local KVM host monitoring: native libvirt exporter + Docker Prometheus/Grafana.
set -euo pipefail
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES="$ROOT/tools/monitoring"
CONFIG_DIR="${KVM_VM_MONITORING_DIR:-/etc/kvm-vm/monitoring}"
SYSTEMD_DIR="${KVM_VM_MONITORING_SYSTEMD_DIR:-/etc/systemd/system}"
UNIT_NAME=kvm-vm-monitoring-exporter.service
EXPORTER_PORT=9179

usage() {
  cat <<'HELP'
Usage: sudo ./tools/setup-monitoring.sh [up|down|status|logs|update|credentials]
       ./tools/setup-monitoring.sh render DIRECTORY

  up          One-click setup (default): install/start local VM exporter and
              Docker Compose Prometheus + Grafana; keep existing data.
  down        Stop the stack and only the exporter service created by this tool.
  status      Show service/container status and local URLs.
  logs        Follow Prometheus/Grafana container logs.
  update      Pull configured image tags and recreate containers (keeps data).
  credentials Print the initial Grafana administrator credentials (root only).
  render DIR  Generate a configuration preview without Docker or systemd.

  Defaults: Grafana http://127.0.0.1:3000, Prometheus 127.0.0.1:9090,
            exporter 127.0.0.1:9179. All ports bind to localhost.
  For first-time setup only: GRAFANA_PORT, PROMETHEUS_PORT,
  PROMETHEUS_RETENTION (default 15d). Edit the saved .env for later changes.

  Existing Compose volumes are NEVER removed by this tool.
HELP
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run as root (sudo ./tools/setup-monitoring.sh $COMMAND)."
}

check_port() {
  local port=$1 name=$2
  [[ $port =~ ^[1-9][0-9]{0,4}$ ]] && (( 10#$port <= 65535 )) ||
    die "$name must be a TCP port from 1 to 65535."
}

check_options() {
  check_port "$GRAFANA_PORT" GRAFANA_PORT
  check_port "$PROMETHEUS_PORT" PROMETHEUS_PORT
  [[ "$GRAFANA_PORT" != "$PROMETHEUS_PORT" ]] || die "Grafana and Prometheus ports must differ."
  [[ "$GRAFANA_PORT" != "$EXPORTER_PORT" && "$PROMETHEUS_PORT" != "$EXPORTER_PORT" ]] ||
    die "Ports must not overlap with exporter port $EXPORTER_PORT."
  [[ $PROMETHEUS_RETENTION =~ ^[1-9][0-9]*(d|h)$ ]] ||
    die "PROMETHEUS_RETENTION must be a positive number followed by d or h (e.g. 15d)."
}

get_setting() {
  local name=$1
  [[ -f "$CONFIG_DIR/.env" ]] || return 1
  sed -n "s/^${name}=//p" "$CONFIG_DIR/.env" | head -n 1
}

seed_file() {
  local relative=$1 target="$CONFIG_DIR/$1"
  if [[ ! -e $target ]]; then
    install -Dm0644 "$TEMPLATES/$relative" "$target"
  fi
}

seed_config() {
  local p
  install -d -m0700 "$CONFIG_DIR"
  for p in \
    compose.yaml prometheus.yml \
    grafana/provisioning/datasources/prometheus.yml \
    grafana/provisioning/dashboards/dashboards.yml \
    grafana/dashboards/kvm-vm.json; do
    seed_file "$p"
  done
  if [[ ! -e "$CONFIG_DIR/.env" ]]; then
    GRAFANA_PORT="${GRAFANA_PORT:-3000}"
    PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
    PROMETHEUS_RETENTION="${PROMETHEUS_RETENTION:-15d}"
    check_options
    local password
    password="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    # Docker Compose reads this file automatically from --project-directory.
    (umask 077; cat > "$CONFIG_DIR/.env" <<ENV
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$password
GRAFANA_PORT=$GRAFANA_PORT
PROMETHEUS_PORT=$PROMETHEUS_PORT
PROMETHEUS_RETENTION=$PROMETHEUS_RETENTION
ENV
    )
    chmod 0600 "$CONFIG_DIR/.env"
    printf 'Created private configuration: %s/.env\n' "$CONFIG_DIR"
  fi
  [[ -f "$CONFIG_DIR/.env" ]] || die "$CONFIG_DIR/.env must be a regular file."
  chmod 0600 "$CONFIG_DIR/.env"
  GRAFANA_PORT="$(get_setting GRAFANA_PORT)"
  PROMETHEUS_PORT="$(get_setting PROMETHEUS_PORT)"
  PROMETHEUS_RETENTION="$(get_setting PROMETHEUS_RETENTION)"
  check_options
}

require_installed_config() {
  [[ -f "$CONFIG_DIR/compose.yaml" && -f "$CONFIG_DIR/.env" ]] ||
    die "Not installed. Run: sudo ./tools/setup-monitoring.sh up"
}

check_docker() {
  command -v docker >/dev/null || die "Docker Engine and the Compose v2 plugin are required."
  docker compose version >/dev/null 2>&1 || die "docker compose v2 is unavailable."
  docker info >/dev/null 2>&1 || die "Cannot connect to Docker Engine; check that docker is running."
}

compose() {
  docker compose --project-directory "$CONFIG_DIR" -f "$CONFIG_DIR/compose.yaml" "$@"
}

find_cli() {
  if [[ -n ${KVM_VM_MONITORING_CLI:-} ]]; then
    KVM_BIN="$KVM_VM_MONITORING_CLI"
  else
    KVM_BIN="$(command -v kvm-vm || true)"
    if [[ -z $KVM_BIN ]]; then
      [[ -f "$ROOT/install.sh" ]] || die "kvm-vm not found. Install it first."
      echo 'Installing kvm-vm from this checkout...'
      "$ROOT/install.sh"
      KVM_BIN=/usr/local/sbin/kvm-vm
    fi
  fi
  [[ $KVM_BIN = /* && -x $KVM_BIN ]] || die "kvm-vm executable must have an absolute path: $KVM_BIN"
  [[ $KVM_BIN != *[[:space:]]* ]] || die "Executable path contains whitespace: $KVM_BIN"
  "$KVM_BIN" exporter --help >/dev/null 2>&1 ||
    die "Installed kvm-vm does not support exporter; rerun sudo ./install.sh."
}

# A previously deployed exporter can be reused. In particular, don't try to
# bind the port a second time when the existing example service is enabled.
exporter_endpoint_exists() {
  python3 - "$EXPORTER_PORT" <<'PY' >/dev/null 2>&1
import sys
from urllib.request import urlopen
with urlopen('http://127.0.0.1:%s/metrics' % sys.argv[1], timeout=2) as reply:
    metrics = reply.read(4096).decode('utf-8', 'replace')
    if 'kvm_vm_collector_success' not in metrics:
        raise SystemExit(1)
PY
}

write_unit() {
  local tmp="$CONFIG_DIR/.exporter-unit.tmp"
  cat > "$tmp" <<UNIT
# Managed by tools/setup-monitoring.sh; read-only libvirt collection.
[Unit]
Description=kvm-vm VM metrics exporter for local Prometheus
After=network.target

[Service]
Type=simple
ExecStart=$KVM_BIN exporter --listen 127.0.0.1 --port $EXPORTER_PORT --interval 5 --timeout 10
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes

[Install]
WantedBy=multi-user.target
UNIT
  if [[ ! -e $SYSTEMD_DIR/$UNIT_NAME ]] || ! cmp -s "$tmp" "$SYSTEMD_DIR/$UNIT_NAME"; then
    install -Dm0644 "$tmp" "$SYSTEMD_DIR/$UNIT_NAME"
    systemctl daemon-reload
  fi
  rm -f "$tmp"
}

start_exporter() {
  if [[ -f "$SYSTEMD_DIR/$UNIT_NAME" ]] && systemctl is-active --quiet "$UNIT_NAME"; then
    systemctl enable --now "$UNIT_NAME"
  elif exporter_endpoint_exists; then
    echo "Reusing existing kvm-vm exporter on localhost:$EXPORTER_PORT (retaining its VM filters)."
  else
    find_cli
    write_unit
    systemctl enable --now "$UNIT_NAME"
  fi
}

up() {
  command -v python3 >/dev/null || die "python3 is required."
  command -v virsh >/dev/null || die "virsh (libvirt-clients) is required."
  command -v systemctl >/dev/null || die "systemd is required for the local exporter service."
  check_docker
  seed_config
  start_exporter
  compose up -d
  printf '\nGrafana:    http://127.0.0.1:%s\n' "$GRAFANA_PORT"
  printf 'Prometheus: http://127.0.0.1:%s\n' "$PROMETHEUS_PORT"
  echo "Exporter:   http://127.0.0.1:$EXPORTER_PORT/metrics"
  echo 'Grafana initial login: sudo ./tools/setup-monitoring.sh credentials'
  echo 'Remote access: use an SSH tunnel or an authenticated HTTPS reverse proxy.'
}

COMMAND="${1:-up}"
case "$COMMAND" in
  -h|--help|help) usage ;;
  render)
    [[ $# -eq 2 && -n $2 ]] || die "Usage: $0 render DIRECTORY"
    CONFIG_DIR="$2"
    command -v python3 >/dev/null || die 'python3 is required.'
    seed_config
    echo "Rendered configuration: $CONFIG_DIR (no services were started)"
    ;;
  up|install|start)
    require_root
    up
    ;;
  down|stop)
    require_root
    require_installed_config
    check_docker
    compose down # preserve named data volumes
    if [[ -f "$SYSTEMD_DIR/$UNIT_NAME" ]]; then
      systemctl disable --now "$UNIT_NAME"
    fi
    ;;
  status)
    require_root
    require_installed_config
    check_docker
    compose ps
    if [[ -f "$SYSTEMD_DIR/$UNIT_NAME" ]]; then
      systemctl --no-pager --plain is-active "$UNIT_NAME" || true
    else
      echo 'Exporter: externally managed (if running).'
    fi
    printf 'Grafana: http://127.0.0.1:%s\n' "$(get_setting GRAFANA_PORT)"
    ;;
  logs)
    require_root
    require_installed_config
    check_docker
    compose logs -f --tail=100
    ;;
  update)
    require_root
    require_installed_config
    check_docker
    compose pull
    up
    ;;
  credentials)
    require_root
    require_installed_config
    printf 'Grafana username: %s\n' "$(get_setting GRAFANA_ADMIN_USER)"
    printf 'Initial Grafana password: %s\n' "$(get_setting GRAFANA_ADMIN_PASSWORD)"
    echo 'If changed in Grafana, the stored initial password is no longer valid.'
    ;;
  *) usage >&2; die "Unknown command: $COMMAND" ;;
esac
