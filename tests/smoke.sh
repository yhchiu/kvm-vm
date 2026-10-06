#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 -m py_compile "$ROOT/kvm-vm"
"$ROOT/kvm-vm" --help >/dev/null
"$ROOT/kvm-vm" --version

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/key.pub" <<'EOF'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK111111111111111111111111111111111111111 smoke@test
EOF
sed "s#file:/root/.ssh/id_ed25519.pub#file:$tmp/key.pub#" \
  "$ROOT/examples/ubuntu24-web01.yaml" > "$tmp/vm.yaml"
"$ROOT/kvm-vm" validate "$tmp/vm.yaml" >/dev/null

echo "smoke: OK"
