#!/usr/bin/env bash
# run_e2e.sh: Pre-flight environment check and runner for kvm-vm real-machine E2E tests.
#
# Usage:
#   sudo bash tests/run_e2e.sh [--distro <name>] [extra pytest options...]
#
# Example:
#   sudo bash tests/run_e2e.sh --distro debian12 -v

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DISTRO="debian12"
EXTRA_ARGS=()

show_help() {
  cat <<'EOF'
Usage: sudo bash tests/run_e2e.sh [options] [-- [pytest options]]

Run real-machine End-to-End (E2E) tests for kvm-vm.

Options:
  --distro <name>     Target distro for VM creation (default: debian12).
                      Supported: debian12, debian13, ubuntu24.04, rocky9, etc.
  -h, --help          Show this help message and exit.

Any additional arguments are forwarded directly to pytest.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --distro)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --distro requires an argument" >&2
        exit 1
      fi
      DISTRO="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      EXTRA_ARGS+=("$1")
      shift
      ;;
  esac
done

echo "=========================================================="
echo " kvm-vm Real-Machine E2E Pre-flight Checks"
echo "=========================================================="

# 1. Operating System
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ERROR: E2E tests require a Linux host (detected: $(uname -s))." >&2
  exit 1
fi

# 2. Root privileges
if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: E2E tests must be run with root privileges (sudo)." >&2
  exit 1
fi

# 3. KVM device
if [[ ! -e /dev/kvm ]]; then
  echo "ERROR: /dev/kvm does not exist. Hardware virtualization (KVM) is required." >&2
  exit 1
fi
if [[ ! -r /dev/kvm || ! -w /dev/kvm ]]; then
  echo "ERROR: /dev/kvm is not readable/writable." >&2
  exit 1
fi

# 4. Required CLI binaries
REQUIRED_CMDS=(virsh virt-install qemu-img virt-sysprep ssh ssh-keygen python3)
MISSING_CMDS=()
for cmd in "${REQUIRED_CMDS[@]}"; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    MISSING_CMDS+=("$cmd")
  fi
done
if [[ ${#MISSING_CMDS[@]} -gt 0 ]]; then
  echo "ERROR: Missing required commands: ${MISSING_CMDS[*]}" >&2
  echo "Install dependencies: apt install libvirt-clients virtinst qemu-utils libguestfs-tools openssh-client" >&2
  exit 1
fi

# 5. Cloud-init seed ISO generator
SEED_GEN=""
for tool in cloud-localds genisoimage mkisofs xorrisofs xorriso; do
  if command -v "$tool" >/dev/null 2>&1; then
    SEED_GEN="$tool"
    break
  fi
done
if [[ -z "$SEED_GEN" ]]; then
  echo "ERROR: Missing cloud-init seed ISO generator." >&2
  echo "Please install cloud-image-utils, genisoimage, or xorriso." >&2
  exit 1
fi

# 6. Python packages (PyYAML, pytest)
if ! python3 -c 'import yaml, pytest' >/dev/null 2>&1; then
  echo "ERROR: Missing required Python packages (yaml, pytest)." >&2
  echo "Please run: pip install pyyaml pytest" >&2
  exit 1
fi

# 7. Libvirt daemon connectivity
LIBVIRT_URI="${KVM_VM_LIBVIRT_URI:-qemu:///system}"
if ! virsh -c "$LIBVIRT_URI" uri >/dev/null 2>&1; then
  echo "ERROR: Cannot connect to libvirt daemon at '$LIBVIRT_URI'." >&2
  echo "Please check if libvirtd (or virtqemud) service is running." >&2
  exit 1
fi

# 8. Check and activate default NAT network if needed
if virsh -c "$LIBVIRT_URI" net-info default >/dev/null 2>&1; then
  if ! virsh -c "$LIBVIRT_URI" net-info default | grep -i -E '^Active:[[:space:]]+yes' >/dev/null 2>&1; then
    echo "NOTICE: Activating libvirt default virtual network..."
    virsh -c "$LIBVIRT_URI" net-start default || true
  fi
else
  echo "WARNING: libvirt 'default' network does not exist. NAT-based test VMs may fail to attach." >&2
fi

echo "Host environment:   $(uname -sr)"
echo "Libvirt URI:        $LIBVIRT_URI"
echo "Seed generator:     $SEED_GEN"
echo "Target distro:      $DISTRO"
echo "Pre-flight checks passed successfully."
echo "=========================================================="
echo " Running E2E Test Suite via pytest"
echo "=========================================================="

export KVM_VM_E2E_DISTRO="$DISTRO"
export KVM_VM_LIBVIRT_URI="$LIBVIRT_URI"

exec python3 -m pytest "$ROOT/tests/test_e2e.py" -m e2e "${EXTRA_ARGS[@]}"
