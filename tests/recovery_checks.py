"""Import the pre-restore Backup check from kvm-vm for the real-machine harnesses.

Operators run `kvm-vm backup check DIR [--until virtnbdbackup.N]` (see
docs/adr/0002-pre-restore-backup-check.md); this module only lets the scripts
in tests/ call the same code without duplicating it.
"""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import sys

_loader = SourceFileLoader("kvm_vm_backup_check", str(Path(__file__).resolve().parent.parent / "kvm-vm"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
_kvm_vm = importlib.util.module_from_spec(_spec)
sys.modules[_loader.name] = _kvm_vm
_loader.exec_module(_kvm_vm)

ChainError = _kvm_vm.BackupChainError
check_backup_chain = _kvm_vm.check_backup_chain
