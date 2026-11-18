#!/usr/bin/env bash
# Remote-write deployments; called by setup-monitoring.sh --mode agent|hybrid.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/tools/setup-monitoring.sh"
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
current_mode() {
  if [[ -f $CONFIG_DIR/.mode.pending ]]; then cat "$CONFIG_DIR/.mode.pending"
  elif [[ -f $CONFIG_DIR/.mode ]]; then cat "$CONFIG_DIR/.mode"
  fi
}
ensure_mode() {
  local active
  active="$(current_mode)"
  [[ -z $active || $active == "$MODE" ]] ||
    fail "Currently deployed in $active mode. Run 'down --mode $active' first (volumes are preserved)."
}
render() {
  python3 "$ROOT/tools/monitoring/configure-remote.py" "$CONFIG_DIR"
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
    lock_monitoring
    render
    ;;
  up|install|start|update)
    require_root
    lock_monitoring
    check_docker
    ensure_mode
    if [[ $COMMAND == update && $(current_mode) != "$MODE" ]]; then
      fail "$MODE mode is not active"
    fi
    check_writer "$MODE"
    render
    if [[ $MODE == hybrid ]]; then seed_config; fi
    if [[ $COMMAND == update ]]; then remote_compose pull; fi
    # Keep the intended mode through a failed or interrupted Compose operation.
    printf '%s\n' "$MODE" > "$CONFIG_DIR/.mode.new"
    mv -f "$CONFIG_DIR/.mode.new" "$CONFIG_DIR/.mode.pending"
    if [[ $MODE == hybrid ]]; then start_exporter
    else "$ROOT/tools/monitoring/ensure-exporter.sh"
    fi
    if [[ ${KVM_VM_REMOTE_RECONFIGURE:-0} == 1 ]]; then
      remote_compose up -d --force-recreate
    else
      remote_compose up -d
    fi
    mv -f "$CONFIG_DIR/.mode.pending" "$CONFIG_DIR/.mode"
    echo "Running in $MODE mode; host identity is stored in $CONFIG_DIR/remote.json."
    ;;
  down|stop)
    require_root
    lock_monitoring
    [[ $(current_mode) == "$MODE" ]] || fail "No active $MODE deployment in $CONFIG_DIR"
    check_docker
    remote_compose down # NEVER delete named volumes
    if [[ -f $SYSTEMD_DIR/$UNIT_NAME ]]; then
      systemctl disable --now "$UNIT_NAME"
    fi
    rm -f "$CONFIG_DIR/.mode" "$CONFIG_DIR/.mode.pending"
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
  *) fail "Unknown command: $COMMAND" ;;
esac
