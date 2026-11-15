#!/usr/bin/env bash
# Idempotently start the existing localhost-only exporter; reuse when active.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYSTEMD_DIR="${KVM_VM_MONITORING_SYSTEMD_DIR:-/etc/systemd/system}"
UNIT_NAME=kvm-vm-monitoring-exporter.service
CONFIG_DIR="${KVM_VM_MONITORING_DIR:-/etc/kvm-vm/monitoring}"
command -v systemctl >/dev/null || { echo 'ERROR: systemd is required' >&2; exit 1; }
command -v virsh >/dev/null || { echo 'ERROR: virsh is required' >&2; exit 1; }

if [[ -f $SYSTEMD_DIR/$UNIT_NAME ]] && systemctl is-active --quiet "$UNIT_NAME"; then
  systemctl enable --now "$UNIT_NAME"
  exit 0
fi
if python3 - <<'PY' >/dev/null 2>&1
from urllib.request import urlopen
with urlopen('http://127.0.0.1:9179/metrics', timeout=2) as response:
    if b'kvm_vm_collector_success' not in response.read(4096):
        raise SystemExit(1)
PY
then
  echo 'Reusing an already running local kvm-vm exporter.'
  exit 0
fi
BIN="${KVM_VM_MONITORING_CLI:-$(command -v kvm-vm || true)}"
if [[ -z $BIN ]]; then
  echo 'Installing kvm-vm exporter from the checkout...'
  "$ROOT/install.sh"
  BIN=/usr/local/sbin/kvm-vm
fi
[[ $BIN = /* && -x $BIN && $BIN != *[[:space:]]* ]] || { echo 'ERROR: invalid exporter CLI path' >&2; exit 1; }
"$BIN" exporter --help >/dev/null 2>&1 || { echo 'ERROR: kvm-vm exporter not installed' >&2; exit 1; }
install -d -m 0700 "$CONFIG_DIR"
TMP="$CONFIG_DIR/.remote-exporter-unit.tmp"
cat > "$TMP" <<UNIT
# Managed by tools/setup-monitoring.sh and setup-remote-monitoring.sh
[Unit]
Description=kvm-vm VM metrics exporter
After=network.target

[Service]
Type=simple
ExecStart=$BIN exporter --listen 127.0.0.1 --port 9179 --interval 5 --timeout 10
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
if [[ ! -f $SYSTEMD_DIR/$UNIT_NAME ]] || ! cmp -s "$TMP" "$SYSTEMD_DIR/$UNIT_NAME"; then
  install -Dm0644 "$TMP" "$SYSTEMD_DIR/$UNIT_NAME"
  systemctl daemon-reload
fi
rm -f "$TMP"
systemctl enable --now "$UNIT_NAME"
