#!/usr/bin/env bash
# Opt-in, disposable KVM central-monitoring lab; never changes host firewall.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$DIR/lab.py" "$@"
