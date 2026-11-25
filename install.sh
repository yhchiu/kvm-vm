#!/bin/sh
set -eu

PREFIX="${PREFIX:-/usr/local}"
DEST="${DEST:-$PREFIX/sbin/kvm-vm}"
DEF_DIR="${KVM_VM_DEF_DIR:-/etc/kvm-vm/definitions}"
TEMPLATE_DIR="${KVM_VM_TEMPLATE_DIR:-/etc/kvm-vm/templates}"
BUILTIN_TEMPLATE_DIR="${KVM_VM_BUILTIN_TEMPLATE_DIR:-$PREFIX/share/kvm-vm/templates}"
SOURCE_DIR="$(dirname "$0")"

if [ "$(id -u)" != 0 ]; then
  echo "ERROR: install.sh must run as root" >&2
  exit 1
fi

install -Dm0755 "$(dirname "$0")/kvm-vm" "$DEST"
BRIDGE_SRC="$(dirname "$0")/tools/kvm-vm-bridge"
BRIDGE_DEST="${BRIDGE_DEST:-$PREFIX/sbin/kvm-vm-bridge}"
if [ -f "$BRIDGE_SRC" ]; then
  install -Dm0755 "$BRIDGE_SRC" "$BRIDGE_DEST"
fi
# Reject untrusted template directories (including existing ancestors) before
# creating or updating vendor files. These paths are used later by a root CLI.
check_template_directory() {
  path=$1
  case "$path" in
    /*) ;;
    *) echo "ERROR: template directory must be absolute: $path" >&2; exit 1 ;;
  esac
  case "$path/" in
    */../*) echo "ERROR: template directory must not contain '..': $path" >&2; exit 1 ;;
  esac
  # Strip trailing slashes so lstat-style shell tests detect directory symlinks.
  while [ "$path" != / ] && [ "${path%/}" != "$path" ]; do path=${path%/}; done
  first=yes
  while :; do
    if [ -L "$path" ]; then
      echo "ERROR: template directory must not be a symbolic link: $path" >&2
      exit 1
    fi
    if [ -e "$path" ]; then
      if [ ! -d "$path" ]; then
        echo "ERROR: not a template directory: $path" >&2; exit 1
      fi
      owner=$(stat -c %u "$path")
      mode=$(stat -c %a "$path")
      if [ "$owner" != 0 ] || { [ "$((0$mode & 022))" -ne 0 ] && { [ "$first" = yes ] || [ "$((0$mode & 01000))" -eq 0 ]; }; }; then
        echo "ERROR: unsafe template directory ownership or permissions: $path" >&2
        exit 1
      fi
    fi
    [ "$path" != / ] || break
    path=$(dirname "$path")
    first=no
  done
}
check_template_directory "$TEMPLATE_DIR"
check_template_directory "$BUILTIN_TEMPLATE_DIR"
if [ "$TEMPLATE_DIR" = "$BUILTIN_TEMPLATE_DIR" ] || [ "$DEF_DIR" = "$BUILTIN_TEMPLATE_DIR" ] || [ "$DEF_DIR" = "$TEMPLATE_DIR" ]; then
  echo "ERROR: template and definition directories must be distinct" >&2
  exit 1
fi
install -d -m0755 "$DEF_DIR"
# Preserve existing local directory metadata and all administrator templates.
if [ ! -d "$TEMPLATE_DIR" ]; then
  install -d -m0755 "$TEMPLATE_DIR"
fi
install -d -m0755 "$BUILTIN_TEMPLATE_DIR"
# Compare physical paths too (including '..' and symlinked ancestors).
local_path=$(cd "$TEMPLATE_DIR" && pwd -P)
builtin_path=$(cd "$BUILTIN_TEMPLATE_DIR" && pwd -P)
definition_path=$(cd "$DEF_DIR" && pwd -P)
if [ "$local_path" = "$builtin_path" ] || [ "$definition_path" = "$builtin_path" ] || [ "$definition_path" = "$local_path" ]; then
  echo "ERROR: template and definition directories must be distinct" >&2
  exit 1
fi
for template in "$SOURCE_DIR"/examples/*.yaml; do
  if [ -L "$template" ]; then
    echo "ERROR: template source must not be a symbolic link: $template" >&2
    exit 1
  fi
  [ -f "$template" ] || continue
  target="$BUILTIN_TEMPLATE_DIR/${template##*/}"
  if [ -L "$target" ] || { [ -e "$target" ] && [ ! -f "$target" ]; }; then
    echo "ERROR: template destination must be a regular file: $target" >&2
    exit 1
  fi
  install -T -m0644 "$template" "$target"
done
install -d -m0755 /var/lib/kvm-vm/state
install -d -m0755 /var/lib/libvirt/images/base
install -d -m0755 /var/lib/libvirt/images/vm
install -d -m0755 /var/lib/libvirt/images/cloud-init

if command -v restorecon >/dev/null 2>&1; then
  restorecon -RF /var/lib/libvirt/images/base /var/lib/libvirt/images/vm /var/lib/libvirt/images/cloud-init || true
fi

echo "Installed: $DEST"
echo "Built-in templates: $BUILTIN_TEMPLATE_DIR"
echo "Local templates: $TEMPLATE_DIR"
if [ -f "$BRIDGE_SRC" ]; then
  echo "Installed: $BRIDGE_DEST"
  echo "Run: kvm-vm --help"
  echo "Run: kvm-vm-bridge --help"
else
  echo "Run: kvm-vm --help"
fi
