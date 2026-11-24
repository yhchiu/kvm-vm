#!/usr/bin/env bash
# Central, localhost-only VictoriaMetrics/Grafana stack. TLS ingress is external.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES="$ROOT/tools/monitoring/central"
CONFIG_DIR="${KVM_VM_CENTRAL_DIR:-/etc/kvm-vm/central}"
COMMAND="${1:-up}"
fail() { echo "ERROR: $*" >&2; exit 1; }
require_root() { [[ ${EUID:-$(id -u)} -eq 0 ]] || fail 'Run with sudo'; }
check_docker() {
  command -v docker >/dev/null || fail 'Docker Engine and Compose v2 are required'
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose v2 not available'
  docker info >/dev/null 2>&1 || fail 'Cannot connect to Docker Engine'
}
check_port() {
  local p=$1
  [[ $p =~ ^[1-9][0-9]{0,4}$ ]] && ((10#$p <= 65535)) || fail 'Invalid TCP port'
}
get_setting() { sed -n "s/^${1}=//p" "$CONFIG_DIR/.env" | head -n 1; }
seed_config() {
  [[ ! -L "$CONFIG_DIR" ]] || fail 'Refusing symlink configuration directory'
  install -d -m 0700 "$CONFIG_DIR"
  [[ ! -L "$CONFIG_DIR/inventory" && ! -L "$CONFIG_DIR/alertmanager/secrets" ]] || fail 'Refusing symlink inventory/secrets directory'
  local file
  for file in compose.yaml nginx-remote-write.example.conf nginx-managed-writers.example.conf nginx-panel-query.example.conf \
      grafana/provisioning/datasources/victoriametrics.yml \
      grafana/provisioning/dashboards/dashboards.yml \
      rules/rules.yml alertmanager/alertmanager.yml \
      grafana/dashboards/fleet.json grafana/dashboards/vms.json \
      grafana/dashboards/hosts.json inventory/scrape.yml inventory/inventory.py; do
    [[ ! -L "$CONFIG_DIR/$file" ]] || fail "Refusing symlink: $file"
    if [[ ! -e $CONFIG_DIR/$file ]]; then
      install -Dm0644 "$TEMPLATES/$file" "$CONFIG_DIR/$file"
    fi
  done
  # Host inventory is data, not a replaceable template. Never truncate it.
  [[ ! -L "$CONFIG_DIR/inventory" && ! -L "$CONFIG_DIR/inventory/hosts.json" ]] || fail 'Refusing symlink inventory'
  install -d -m 0755 "$CONFIG_DIR/inventory"
  install -d -m 0755 "$CONFIG_DIR/alertmanager/secrets"
  if [[ ! -e "$CONFIG_DIR/inventory/hosts.json" ]]; then
    printf '[]\n' > "$CONFIG_DIR/inventory/hosts.json"
    chmod 0644 "$CONFIG_DIR/inventory/hosts.json"
  fi
  if [[ ! -e "$CONFIG_DIR/.env" ]]; then
    local gp vp retention password
    gp="${GRAFANA_PORT:-3000}"
    vp="${VM_PORT:-8428}"
    retention="${VM_RETENTION:-30d}"
    check_port "$gp"; check_port "$vp"
    [[ $gp != "$vp" ]] || fail 'Grafana and VictoriaMetrics ports must differ'
    [[ $retention =~ ^[1-9][0-9]*(h|d|w|y)$ ]] || fail 'VM_RETENTION must be a positive duration (e.g. 30d)'
    password="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    cat > "$CONFIG_DIR/.env" <<EOF
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$password
GRAFANA_PORT=$gp
VM_PORT=$vp
VM_RETENTION=$retention
EOF
    chmod 0600 "$CONFIG_DIR/.env"
  fi
  [[ -f "$CONFIG_DIR/.env" ]] || fail 'Missing regular .env'
  [[ ! -L "$CONFIG_DIR/.env" ]] || fail "Refusing symlink .env"
  chmod 0600 "$CONFIG_DIR/.env"
  check_port "$(get_setting GRAFANA_PORT)"
  check_port "$(get_setting VM_PORT)"
  [[ $(get_setting GRAFANA_PORT) != "$(get_setting VM_PORT)" ]] || fail 'Port collision'
  [[ $(get_setting VM_RETENTION) =~ ^[1-9][0-9]*(h|d|w|y)$ ]] || fail 'Invalid VM_RETENTION'
}
require_config() {
  [[ -f "$CONFIG_DIR/.env" && -f "$CONFIG_DIR/compose.yaml" ]] || fail 'Not installed; run up'
}
compose() { docker compose --project-directory "$CONFIG_DIR" -f "$CONFIG_DIR/compose.yaml" "$@"; }
case "$COMMAND" in
  help|--help|-h)
    cat <<'HELP'
Usage: sudo ./tools/setup-central-monitoring.sh [up|reload|status|logs|update|down|credentials|enroll HOST]
       sudo ./tools/setup-central-monitoring.sh inventory [list|add UUID HOST SITE [--tenant ID]|set-tenant UUID ID|disable UUID|enable UUID|remove UUID]
       sudo ./tools/setup-central-monitoring.sh writers --help  # optional managed writer credentials
       sudo ./tools/setup-central-monitoring.sh registry --help  # operator-only VM/tenant metadata
       sudo ./tools/setup-central-monitoring.sh notifications webhook URL_FILE
       sudo ./tools/setup-central-monitoring.sh notifications email TO SMTP_HOST:PORT FROM SMTP_USER PASSWORD_FILE
       ./tools/setup-central-monitoring.sh customer-readiness  # safe preflight, fails closed
       sudo ./tools/setup-central-monitoring.sh sync-templates  # reviewed refresh of bundled stack templates
       ./tools/setup-central-monitoring.sh render DIRECTORY
This installs a LOCALHOST-ONLY VictoriaMetrics + Grafana + vmalert + Alertmanager.
Remote hosts require a separately installed TLS + Basic Auth ingress (see docs).
First-run settings: GRAFANA_PORT=3000 VM_PORT=8428 VM_RETENTION=30d.
Existing volumes, .env and dashboard customizations are preserved.
HELP
    ;;
  render)
    [[ $# -eq 2 && -n $2 ]] || fail 'render requires destination path'
    CONFIG_DIR="$2"
    seed_config
    echo "Central configuration rendered: $CONFIG_DIR"
    ;;
  up|install|start)
    require_root
    check_docker
    seed_config
    compose up -d
    echo "Grafana: http://127.0.0.1:$(get_setting GRAFANA_PORT)"
    echo "VictoriaMetrics API (LOOPBACK ONLY): http://127.0.0.1:$(get_setting VM_PORT)"
    echo 'Remote write requires a separate authenticated HTTPS ingress. See docs/central-monitoring.md.'
    ;;
  reload)
    require_root; require_config; check_docker
    # Recreate containers after a bind-mounted config file was atomically
    # replaced (Docker retains the old inode for an individual file mount).
    compose up -d --force-recreate
    ;;
  status)
    require_root; require_config; check_docker
    compose ps
    ;;
  logs)
    require_root; require_config; check_docker
    compose logs -f --tail=100
    ;;
  update)
    require_root; require_config; check_docker
    compose pull
    compose up -d
    ;;
  down|stop)
    require_root; require_config; check_docker
    compose down # Explicitly no -v: retain named volumes.
    ;;
  customer-readiness)
    # Operator visibility only. Single-node VictoriaMetrics has no native
    # tenant boundary; even the optional managed writer ingress is not
    # a verified tenant authorization boundary. Never infer readiness
    # from tenant metadata, Grafana folders or dashboard variables.
    cat <<'READINESS'
BLOCKED: Hosting customer data access is NOT enabled.
- VictoriaMetrics Single has no native tenant isolation.
- Legacy Remote Write Basic Auth authenticates a sender only. The optional
  managed writer ingress is Host-bound, not a tenant authorization boundary.
- Central Grafana currently has an operator-wide datasource.
Requirements: native tenant-aware storage/routing, trusted ingress tenant
assignment, restricted query datasource for each tenant, negative isolation
tests, and audited customer login before this check may be enabled.
READINESS
    exit 2
    ;;
  inventory)
    require_root; require_config
    shift
    [[ $# -gt 0 ]] || fail 'inventory requires a subcommand'
    # A pre-tenant inventory exporter deployed in the running container would
    # reject the new tenant_id field and make expected-host alerts disappear.
    # Require an explicit reviewed template upgrade before the first mutation.
    tenant_change=0
    [[ $1 != set-tenant ]] || tenant_change=1
    for argument in "$@"; do
      [[ $argument != --tenant ]] || tenant_change=1
    done
    if [[ $tenant_change == 1 ]] && ! cmp -s "$TEMPLATES/inventory/inventory.py" "$CONFIG_DIR/inventory/inventory.py"; then
      fail 'Tenant inventory template is not yet deployed: review sync-templates, then reload before assigning tenants.'
    fi
    python3 "$TEMPLATES/inventory/inventory.py" --file "$CONFIG_DIR/inventory/hosts.json" "$@"
    ;;
  registry)
    require_root; require_config
    [[ ! -L "$CONFIG_DIR" && ! -L "$CONFIG_DIR/inventory" && ! -L "$CONFIG_DIR/registry" ]] || fail "Refusing symlink registry location"
    shift
    python3 "$TEMPLATES/registry.py" --file "$CONFIG_DIR/registry/ownership.json" --hosts "$CONFIG_DIR/inventory/hosts.json" "$@"
    ;;
  writers)
    require_root; require_config
    shift
    python3 "$TEMPLATES/writers.py" --directory "${KVM_VM_WRITER_DIR:-/etc/nginx/kvm-vm-writers}" --hosts "$CONFIG_DIR/inventory/hosts.json" "$@"
    ;;
  notifications)
    require_root; require_config
    shift
    python3 "$TEMPLATES/configure-notifications.py" "$CONFIG_DIR" "$@"
    ;;
  sync-templates)
    require_root; require_config
    seed_config  # Seed new inventory files before enabling mounts in compose.
    # Explicit migration: preserve locally modified settings and secrets.
    # Create backup BEFORE overwriting any template. Run up afterwards.
    backup="$CONFIG_DIR/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    install -d -m 0700 "$backup"
    for file in compose.yaml rules/rules.yml grafana/dashboards/fleet.json \
        grafana/dashboards/vms.json grafana/dashboards/hosts.json inventory/inventory.py inventory/scrape.yml; do
      [[ ! -L "$CONFIG_DIR/$file" ]] || fail "Refusing symlink: $file"
      if [[ -f "$CONFIG_DIR/$file" ]]; then
        install -Dm0600 "$CONFIG_DIR/$file" "$backup/$file"
      fi
      install -Dm0644 "$TEMPLATES/$file" "$CONFIG_DIR/$file"
    done
    echo "Refreshed stack templates (backup: $backup). Run reload to apply."
    ;;
  credentials)
    require_root; require_config
    printf 'Grafana username: %s\n' "$(get_setting GRAFANA_ADMIN_USER)"
    printf 'Initial Grafana password: %s\n' "$(get_setting GRAFANA_ADMIN_PASSWORD)"
    ;;
  enroll)
    require_root; require_config
    [[ $# -eq 2 && $2 =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || fail 'Provide alphanumeric host username'
    check_docker
    user="$2"
    file="$CONFIG_DIR/htpasswd"
    [[ ! -L $file ]] || fail 'Refusing symlink htpasswd file'
    if [[ -f $file ]] && awk -F: -v target="$user" '$1 == target { found=1 } END { exit !found }' "$file"; then
      fail "Host username already enrolled: $user. Revoke before re-enrolling."
    fi
    password="$(python3 -c 'import secrets; print(secrets.token_urlsafe(36))')"
    # Password via stdin, not process args; bcrypt hash only is stored on disk.
    hash="$(printf '%s\n' "$password" | docker run --rm -i --entrypoint htpasswd httpd:2.4-alpine -niB "$user")"
    [[ $hash == "$user:"* && $hash == *'$2'* ]] || fail 'Failed to generate bcrypt htpasswd entry'
    printf '%s\n' "$hash" >> "$file"
    chmod 0600 "$file"
    printf 'Username: %s\nPassword (shown once): %s\n' "$user" "$password"
    echo 'Copy updated htpasswd to the nginx Basic Auth path and reload nginx.'
    ;;
  *) fail "Unknown command: $COMMAND" ;;
esac
