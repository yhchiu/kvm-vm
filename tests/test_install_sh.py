"""Exercise the installer under POSIX shells without touching system files."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [('sh',), ('dash',), ('busybox', 'ash'), ('bash', '--posix')]
DIRECTORIES = [
    '/var/lib/kvm-vm/state',
    '/var/lib/libvirt/images/base',
    '/var/lib/libvirt/images/vm',
    '/var/lib/libvirt/images/cloud-init',
]


@pytest.fixture(params=SHELLS, ids=['sh', 'dash', 'busybox-ash', 'bash-posix'])
def installer(tmp_path, request):
    shell, *options = request.param
    executable = shutil.which(shell)
    dirname = shutil.which('dirname')
    if os.name != 'posix' or not executable or not dirname or not Path('/bin/sh').exists():
        pytest.skip('required POSIX shell or utility is unavailable')
    source = tmp_path / 'source with spaces'
    source.mkdir()
    shutil.copy2(ROOT / 'install.sh', source / 'install.sh')
    (source / 'kvm-vm').touch()
    shutil.copytree(ROOT / 'examples', source / 'examples')
    (source / 'tools').mkdir()
    bridge = source / 'tools/kvm-vm-bridge'
    bridge.touch()
    shutil.copy2(ROOT / "tools/kvm-vm-check", source / "tools/kvm-vm-check")
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'dirname').symlink_to(dirname)
    real_install = shutil.which('install')
    real_stat = shutil.which('stat')
    if not real_install or not real_stat:
        pytest.skip('GNU coreutils required')
    log = tmp_path / 'calls'

    def stub(name, body):
        path = bin_dir / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    stub('id', 'printf "%s\\n" "$TEST_UID"\n')
    # Only real writes to the isolated temporary directory are permitted.
    stub('install', 'printf "%s\\n" install "$@" >> "$TEST_LOG"\n'
         '[ "$INSTALL_STATUS" = 0 ] || exit "$INSTALL_STATUS"\n'
         'for target do :; done\n'
         'case "$target" in "$TEST_ROOT"/*) exec "$REAL_INSTALL" "$@";; esac\n')
    # Simulate root ownership for CI running as an ordinary user. Modes and
    # actual symlink/file checks still come from the real filesystem.
    stub('stat', 'if [ "$2" = %u ]; then echo 0; else exec "$REAL_STAT" "$@"; fi\n')
    stub('restorecon', 'printf "%s\\n" restorecon "$@" >> "$TEST_LOG"\nexit "$RESTORECON_STATUS"\n')

    def run(*, uid='0', install_status='0', restorecon_status='0', **overrides):
        log.unlink(missing_ok=True)
        # Restrict PATH to mocks; real writes are confined to the temporary tree.
        env = {**os.environ, 'PATH': str(bin_dir), 'TEST_LOG': str(log),
               'TEST_UID': uid, 'INSTALL_STATUS': install_status,
               'RESTORECON_STATUS': restorecon_status, 'TEST_ROOT': str(tmp_path),
               'REAL_INSTALL': real_install, 'REAL_STAT': real_stat}
        for name in ('PREFIX', 'DEST', 'BRIDGE_DEST', 'CHECK_DEST', 'EUID', 'BASH_ENV', 'ENV', 'KVM_VM_DEF_DIR', 'KVM_VM_TEMPLATE_DIR',
                     'KVM_VM_BUILTIN_TEMPLATE_DIR'):
            env.pop(name, None)
        env.update(KVM_VM_DEF_DIR=str(tmp_path / 'definitions'),
                   KVM_VM_TEMPLATE_DIR=str(tmp_path / 'local'),
                   KVM_VM_BUILTIN_TEMPLATE_DIR=str(tmp_path / 'builtin'))
        env.update(overrides)
        result = subprocess.run([executable, *options, str(source / 'install.sh')],
                                cwd=tmp_path, env=env, capture_output=True, text=True,
                                timeout=10)
        return result, log.read_text().splitlines() if log.exists() else []

    return run, source, bridge, bin_dir


def test_default_install(installer):
    run, source, bridge, _ = installer
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert calls == [
        'install', '-Dm0755', str(source / 'kvm-vm'), '/usr/local/sbin/kvm-vm',
        'install', '-Dm0755', str(bridge), '/usr/local/sbin/kvm-vm-bridge',
        'install', '-Dm0755', str(source / 'tools/kvm-vm-check'), '/usr/local/sbin/kvm-vm-check',
        *[arg for directory in ('definitions', 'local', 'builtin')
          for arg in ('install', '-d', '-m0755', str(source.parent / directory))],
        *[arg for file in sorted((source / 'examples').glob('*.yaml'))
          for arg in ('install', '-T', '-m0644', str(file), str(source.parent / 'builtin' / file.name))],
        *[arg for directory in DIRECTORIES for arg in ('install', '-d', '-m0755', directory)],
        'restorecon', '-RF', *DIRECTORIES[1:],
    ]
    assert 'Installed: /usr/local/sbin/kvm-vm-bridge' in result.stdout
    assert 'Installed: /usr/local/sbin/kvm-vm-check' in result.stdout
    assert 'Run: kvm-vm-check --help' in result.stdout
    assert sorted(p.name for p in (source.parent / 'builtin').iterdir()) == sorted(p.name for p in (source / 'examples').glob('*.yaml'))


def test_custom_paths_and_optional_bridge(installer):
    run, source, bridge, bin_dir = installer
    result, calls = run(PREFIX='/opt/custom prefix', DEST='/opt/custom destination',
                        BRIDGE_DEST='/opt/custom bridge', CHECK_DEST='/opt/custom check')
    assert result.returncode == 0, result.stderr
    assert calls[:12] == ['install', '-Dm0755', str(source / 'kvm-vm'), '/opt/custom destination',
                        'install', '-Dm0755', str(bridge), '/opt/custom bridge',
                        'install', '-Dm0755', str(source / 'tools/kvm-vm-check'), '/opt/custom check']
    bridge.unlink()
    (bin_dir / 'restorecon').unlink()
    result, calls = run(PREFIX='/opt/custom prefix')
    assert result.returncode == 0, result.stderr
    assert calls[:4] == ['install', '-Dm0755', str(source / 'kvm-vm'), '/opt/custom prefix/sbin/kvm-vm']
    assert calls[4:8] == ['install', '-Dm0755', str(source / 'tools/kvm-vm-check'),
                          '/opt/custom prefix/sbin/kvm-vm-check']
    assert str(bridge) not in calls and 'restorecon' not in calls
    assert 'kvm-vm-bridge' not in result.stdout


def test_root_check_and_install_error(installer):
    run, source, _, _ = installer
    result, calls = run(uid='1000', EUID='0')
    assert result.returncode == 1
    assert 'must run as root' in result.stderr
    assert not calls
    result, calls = run(install_status='7')
    assert result.returncode == 7
    assert calls == ['install', '-Dm0755', str(source / 'kvm-vm'), '/usr/local/sbin/kvm-vm']
    assert 'Installed:' not in result.stdout


def test_restorecon_failure_remains_nonfatal(installer):
    run, _, _, _ = installer
    result, calls = run(restorecon_status='1')
    assert result.returncode == 0, result.stderr
    assert 'restorecon' in calls
    assert 'Installed:' in result.stdout


def test_upgrade_preserves_local_and_updates_builtin(installer):
    run, source, _, _ = installer
    assert run()[0].returncode == 0
    local = source.parent / 'local' / 'ubuntu24-nat.yaml'
    local.write_text('administrator content\n')
    vendor = source.parent / 'builtin' / local.name
    vendor.write_text('old vendor content\n')
    result, _ = run()
    assert result.returncode == 0, result.stderr
    assert local.read_text() == 'administrator content\n'
    assert vendor.read_bytes() == (source / 'examples' / local.name).read_bytes()
    assert vendor.stat().st_mode & 0o777 == 0o644


def test_prefix_chooses_vendor_dir(installer):
    run, source, _, _ = installer
    prefix = source.parent / 'custom prefix'
    result, _ = run(PREFIX=str(prefix), KVM_VM_BUILTIN_TEMPLATE_DIR='')
    assert result.returncode == 0, result.stderr
    assert (prefix / 'share/kvm-vm/templates/ubuntu24-nat.yaml').is_file()
    check = prefix / 'sbin/kvm-vm-check'
    assert check.read_bytes() == (source / 'tools/kvm-vm-check').read_bytes()
    assert check.stat().st_mode & 0o777 == 0o755
    result = subprocess.run([str(check), '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'usage: kvm-vm-check' in result.stdout


def test_reject_unsafe_template_directories(installer):
    run, source, _, _ = installer
    local = source.parent / 'local'
    local.mkdir(mode=0o777)
    local.chmod(0o777)
    result, _ = run()
    assert result.returncode != 0
    assert 'unsafe template directory' in result.stderr
    local.rmdir()
    local.symlink_to(source, target_is_directory=True)
    result, _ = run()
    assert result.returncode != 0
    assert 'symbolic link' in result.stderr


def test_reject_same_vendor_local(installer):
    run, source, _, _ = installer
    result, _ = run(KVM_VM_BUILTIN_TEMPLATE_DIR=str(source.parent / 'local'))
    assert result.returncode != 0
    assert 'must be distinct' in result.stderr


def test_reject_vendor_symlink(installer):
    run, source, _, _ = installer
    vendor = source.parent / 'builtin'
    vendor.mkdir()
    target = source.parent / 'untouched'
    target.write_text('do not change')
    (vendor / 'almalinux10-nat.yaml').symlink_to(target)
    result, _ = run()
    assert result.returncode != 0
    assert target.read_text() == 'do not change'
