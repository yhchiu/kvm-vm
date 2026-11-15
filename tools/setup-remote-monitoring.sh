#!/usr/bin/env bash
# Remote-write deployments; called by setup-monitoring.sh --mode agent|hybrid.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-}"
shift || true
COMMAND="${1:-up}"
[[ $MODE == agent || $MODE == hybrid ]] || { echo 'ERROR: mode must be agent or hybrid' >&2; exit 1; }
CONFIG_DIR="${KVM_VM_MONITORING_DIR:-/etc/kvm-vm/monitoring}"
SYSTEMD_DIR="${KVM_VM_MONITORING_SYSTEMD_DIR:-/etc/systemd/system}"
UNIT_NAME=kvm-vm-monitoring-exporter.service

fail() { echo "ERROR: $*" >&2; exit 1; }
require_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'Run with sudo'; }
check_docker() {
  command -v docker >/dev/null || fail 'Docker Engine and Compose v2 required'
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 unavailable'
  docker info >/dev/null 2>&1 || fail 'Cannot connect to Docker Engine'
}
current_mode() { [[ ! -f "$CONFIG_DIR/.mode" ]] || cat "$CONFIG_DIR/.mode"; }
ensure_mode() {
  local active
  active="$(current_mode)"
  [[ -z $active || $active == "$MODE" ]] ||
    fail "Currently deployed in $active mode. Run 'down --mode $active' first (volumes are preserved)."
}
render() {
  python3 "$ROOT/tools/monitoring/configure-remote.py" "$CONFIG_DIR"
}
local_compose() {
  docker compose --project-directory "$CONFIG_DIR" -f "$CONFIG_DIR/compose.yaml" "$@"
}
remote_compose() {
  if [[ $MODE == agent ]]; then
    docker compose --project-directory "$CONFIG_DIR" --env-file "$CONFIG_DIR/remote.env" \
      -f "$CONFIG_DIR/compose.agent.yaml" "$@"
  else
    docker compose --project-directory "$CONFIG_DIR" \
      --env-file "$CONFIG_DIR/.env" --env-file "$CONFIG_DIR/remote.env" \
      -f "$CONFIG_DIR/compose.yaml" -f "$CONFIG_DIR/compose.hybrid.yaml" "$@"
  fi
}
if [[ $COMMAND == --help || $COMMAND == help || $COMMAND == -h ]]; then
  cat <<HELP
Usage: sudo ./tools/setup-monitoring.sh [up|down|status|logs|update] --mode $MODE
  For first deployment set KVM_VM_REMOTE_WRITE_URL (https://.../api/v1/write),
  KVM_VM_REMOTE_PASSWORD_FILE, and optionally KVM_VM_REMOTE_USERNAME,
  KVM_VM_MONITOR_HOST, KVM_VM_MONITOR_SITE, KVM_VM_MONITOR_HOST_ID.
  Render without Docker: ... render DIRECTORY --mode $MODE
  Modes must be stopped before switching; volumes and host identity remain saved.
HELP
  exit 0
fi
case "$COMMAND" in
  render)
    [[ $# -eq 2 && -n $2 ]] || fail 'render requires a destination directory'
    CONFIG_DIR="$2"
    render
    ;;
  up|install|start)
    require_root
    check_docker
    ensure_mode
    if [[ $MODE == agent && -z $(current_mode) && -e "$CONFIG_DIR/compose.yaml" ]]; then
      # Never silently keep local Prometheus running while switching to agent.
      if [[ -n $(local_compose ps --status running -q) ]]; then
        fail 'Local Prometheus/Grafana is running. Stop it with local down before switching to agent.'
      fi
    fi
    render
    if [[ $MODE == hybrid && -z $(current_mode) ]]; then
      # Reuse the native local bootstrap. It seeds Grafana and the local TSDB.
      "$ROOT/tools/setup-monitoring.sh" up
    else
      "$ROOT/tools/monitoring/ensure-exporter.sh"
    fi
    if [[ ${KVM_VM_REMOTE_RECONFIGURE:-0} == 1 ]]; then
      remote_compose up -d --force-recreate
    else
      remote_compose up -d
    fi
    printf '%s\n' "$MODE" > "$CONFIG_DIR/.mode"
    chmod 0600 "$CONFIG_DIR/.mode"
    echo "Running in $MODE mode; host identity is stored in $CONFIG_DIR/remote.json."
    ;;
  down|stop)
    require_root
    [[ $(current_mode) == "$MODE" ]] || fail "No active $MODE deployment in $CONFIG_DIR"
    check_docker
    remote_compose down # NEVER delete named volumes
    rm -f "$CONFIG_DIR/.mode"
    if [[ -f $SYSTEMD_DIR/$UNIT_NAME ]]; then
      systemctl disable --now "$UNIT_NAME"
    fi
    ;;
  status)
    require_root
    [[ $(current_mode) == "$MODE" ]] || fail "$MODE mode is not active"
    check_docker
    remote_compose ps
    [[ $MODE != agent ]] || echo 'vmagent self-metrics: http://127.0.0.1:8429/metrics'
    ;;
  logs)
    require_root
    [[ $(current_mode) == "$MODE" ]] || fail "$MODE mode is not active"
    check_docker
    remote_compose logs -f --tail=100
    ;;
  update)
    require_root
    [[ $(current_mode) == "$MODE" ]] || fail "$MODE mode is not active"
    check_docker
    remote_compose pull
    "$ROOT/tools/setup-remote-monitoring.sh" "$MODE" up
    ;;
  *) fail "Unknown command: $COMMAND" ;;
esac
