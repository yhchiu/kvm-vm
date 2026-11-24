"""Exercise the installer under POSIX shells without touching system files."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHELLS = [('sh',), ('dash',), ('busybox', 'ash'), ('bash', '--posix')]
DIRECTORIES = [
    '/etc/kvm-vm/definitions',
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
    (source / 'tools').mkdir()
    bridge = source / 'tools/kvm-vm-bridge'
    bridge.touch()
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'dirname').symlink_to(dirname)
    log = tmp_path / 'calls'

    def stub(name, body):
        path = bin_dir / name
        path.write_text('#!/bin/sh\n' + body)
        path.chmod(0o755)

    stub('id', 'printf "%s\\n" "$TEST_UID"\n')
    stub('install', 'printf "%s\\n" install "$@" >> "$TEST_LOG"\nexit "$INSTALL_STATUS"\n')
    stub('restorecon', 'printf "%s\\n" restorecon "$@" >> "$TEST_LOG"\nexit "$RESTORECON_STATUS"\n')

    def run(*, uid='0', install_status='0', restorecon_status='0', **overrides):
        log.unlink(missing_ok=True)
        # Restrict PATH to mocks and dirname: no real installation is possible.
        env = {**os.environ, 'PATH': str(bin_dir), 'TEST_LOG': str(log),
               'TEST_UID': uid, 'INSTALL_STATUS': install_status,
               'RESTORECON_STATUS': restorecon_status}
        for name in ('PREFIX', 'DEST', 'BRIDGE_DEST', 'EUID', 'BASH_ENV', 'ENV'):
            env.pop(name, None)
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
        *[arg for directory in DIRECTORIES for arg in ('install', '-d', '-m0755', directory)],
        'restorecon', '-RF', *DIRECTORIES[2:],
    ]
    assert 'Installed: /usr/local/sbin/kvm-vm-bridge' in result.stdout


def test_custom_paths_and_optional_bridge(installer):
    run, source, bridge, bin_dir = installer
    result, calls = run(PREFIX='/opt/custom prefix', DEST='/opt/custom destination',
                        BRIDGE_DEST='/opt/custom bridge')
    assert result.returncode == 0, result.stderr
    assert calls[:8] == ['install', '-Dm0755', str(source / 'kvm-vm'), '/opt/custom destination',
                        'install', '-Dm0755', str(bridge), '/opt/custom bridge']
    bridge.unlink()
    (bin_dir / 'restorecon').unlink()
    result, calls = run(PREFIX='/opt/custom prefix')
    assert result.returncode == 0, result.stderr
    assert calls[:4] == ['install', '-Dm0755', str(source / 'kvm-vm'), '/opt/custom prefix/sbin/kvm-vm']
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
