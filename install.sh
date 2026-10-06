#!/usr/bin/env bash
set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"
DEST="${DEST:-$PREFIX/sbin/kvm-vm}"

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "ERROR: install.sh must run as root" >&2
  exit 1
fi

install -Dm0755 "$(dirname "$0")/kvm-vm" "$DEST"
install -d -m0755 /etc/kvm-vm/definitions
install -d -m0755 /var/lib/kvm-vm/state
install -d -m0755 /var/lib/libvirt/images/base
install -d -m0755 /var/lib/libvirt/images/vm
install -d -m0755 /var/lib/libvirt/images/cloud-init

if command -v restorecon >/dev/null 2>&1; then
  restorecon -RF /var/lib/libvirt/images/base /var/lib/libvirt/images/vm /var/lib/libvirt/images/cloud-init || true
fi

echo "Installed: $DEST"
echo "Run: kvm-vm --help"
