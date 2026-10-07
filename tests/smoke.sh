#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m py_compile "$ROOT/kvm-vm"
"$ROOT/kvm-vm" --help >/dev/null
"$ROOT/kvm-vm" reinstall --help >/dev/null
"$ROOT/kvm-vm" --version

if python3 -m pytest --version >/dev/null 2>&1; then
  python3 -m pytest "$ROOT/tests/test_unit.py" -q
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/key.pub" <<'KEY'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK111111111111111111111111111111111111111 smoke@test
KEY

# Bridge example remains valid and uses br0.
sed "s#file:/root/.ssh/id_ed25519.pub#file:$tmp/key.pub#" \
  "$ROOT/examples/ubuntu24-web01.yaml" > "$tmp/bridge.yaml"
"$ROOT/kvm-vm" validate "$tmp/bridge.yaml" > "$tmp/bridge.out"
grep -q '^  mode: bridge$' "$tmp/bridge.out"
grep -q '^  bridge: br0$' "$tmp/bridge.out"

# Omitting both mode and bridge must normalize to bridge + br0.
cat > "$tmp/default.yaml" <<EOF2
version: 1
vm:
  name: defaultnet01
storage:
  image:
    distro: ubuntu24.04
network:
  ipv4:
    method: dhcp
cloud_init:
  ssh_authorized_keys:
    - file:$tmp/key.pub
EOF2
"$ROOT/kvm-vm" validate "$tmp/default.yaml" > "$tmp/default.out"
grep -q '^  mode: bridge$' "$tmp/default.out"
grep -q '^  bridge: br0$' "$tmp/default.out"

# KVM_VM_BRIDGE overrides default bridge.
KVM_VM_BRIDGE="custombr9" "$ROOT/kvm-vm" validate "$tmp/default.yaml" > "$tmp/custombr.out"
grep -q '^  bridge: custombr9$' "$tmp/custombr.out"

# NAT example normalizes to the named libvirt virtual network. Validation is offline and
# intentionally does not require a running libvirt daemon; create/clone do runtime checks.
sed "s#file:/root/.ssh/id_ed25519.pub#file:$tmp/key.pub#" \
  "$ROOT/examples/ubuntu24-nat.yaml" > "$tmp/nat.yaml"
"$ROOT/kvm-vm" validate "$tmp/nat.yaml" > "$tmp/nat.out"
grep -q '^  mode: nat$' "$tmp/nat.out"
grep -q '^  libvirt_network: default$' "$tmp/nat.out"

# Reject an invalid network mode.
sed 's/mode: nat/mode: routed/' "$tmp/nat.yaml" > "$tmp/bad.yaml"
if "$ROOT/kvm-vm" validate "$tmp/bad.yaml" >/dev/null 2>&1; then
  echo "ERROR: invalid network mode unexpectedly validated" >&2
  exit 1
fi

echo "smoke: OK"
