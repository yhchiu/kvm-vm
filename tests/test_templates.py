"""Template resolver, CLI and shipped YAML tests (no libvirt required)."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('template_kvm_vm', str(ROOT / 'kvm-vm'))
spec = importlib.util.spec_from_loader(loader.name, loader)
kvm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kvm)


def raw(name='example01'):
    return {'version': 1, 'vm': {'name': name},
            'storage': {'image': {'distro': 'debian13'}},
            'cloud_init': {'ssh_authorized_keys': ['ssh-ed25519 AAAA test']}}


@pytest.fixture
def roots(tmp_path, monkeypatch):
    local, builtin = tmp_path / 'local', tmp_path / 'builtin'
    local.mkdir(); builtin.mkdir()
    monkeypatch.setattr(kvm, 'TEMPLATE_DIR', local)
    monkeypatch.setattr(kvm, 'BUILTIN_TEMPLATE_DIR', builtin)
    monkeypatch.setattr(kvm, 'STATE_DIR', tmp_path / 'state')
    kvm.yaml_dump(builtin / 'example.yaml', raw('vendor01'))
    return local, builtin


@pytest.mark.parametrize('command', [['validate'], ['create'], ['clone', 'source01']])
@pytest.mark.parametrize('reference', [['@example'], ['@example.yaml'], ['--template', 'example'], ['--template', 'example.yaml']])
def test_parser_and_resolver(roots, command, reference):
    args = kvm.build_parser().parse_args(command + reference)
    assert kvm.resolve_definition_path(args) == (roots[1] / 'example.yaml', True)


@pytest.mark.parametrize('command', [['validate'], ['create'], ['clone', 'source01']])
def test_input_required_and_mutually_exclusive(command):
    for extra in ([], ['vm.yaml', '--template', 'example'], ['@example', '--template', 'example']):
        with pytest.raises(SystemExit) as exc:
            kvm.build_parser().parse_args(command + extra)
        assert exc.value.code == 2


def test_local_precedence_and_listing(roots, capsys):
    local, builtin = roots
    kvm.yaml_dump(local / 'example.yaml', raw('local01'))
    clone = raw(); clone.pop('storage')
    kvm.yaml_dump(builtin / 'clone.yaml', clone)
    (local / 'broken.yaml').write_text('invalid: [\n')
    assert kvm.find_template('example') == (local / 'example.yaml', 'local')
    assert kvm.cmd_template_list(kvm.build_parser().parse_args(['template', 'list'])) == 0
    rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert rows == [['NAME', 'SOURCE', 'TYPE'], ['broken', 'local', 'invalid'],
                    ['clone', 'builtin', 'clone'], ['example', 'local', 'create']]
    assert kvm.cmd_template_show(kvm.build_parser().parse_args(['template', 'show', 'example.yaml'])) == 0
    assert capsys.readouterr().out == (local / 'example.yaml').read_text()


def test_no_implicit_fallback(roots, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = kvm.build_parser().parse_args(['validate', 'example.yaml'])
    assert kvm.resolve_definition_path(args) == (Path('example.yaml'), False)
    with pytest.raises(kvm.KVMError, match='Definition not found'):
        kvm.cmd_validate(args)
    assert kvm.resolve_definition_path(kvm.build_parser().parse_args(['validate', './@example.yaml'])) == (Path('@example.yaml'), False)
    with pytest.raises(kvm.KVMError, match='Template not found'):
        kvm.find_template('missing')


@pytest.mark.parametrize('name', ['', '.', '..', '../example', '/tmp/example', 'x/y', r'x\y', '@example', 'x..y', 'example.yml', 'example.yaml.yaml', '-bad'])
def test_reject_bad_names(roots, name):
    with pytest.raises(kvm.KVMError, match='Invalid template name'):
        kvm.find_template(name)


@pytest.mark.parametrize('mode', ['symlink', 'dangling', 'directory', 'fifo', 'writable', 'unowned'])
def test_unsafe_local_never_falls_back(roots, tmp_path, mode):
    local, builtin = roots
    target = local / 'example.yaml'
    if mode == 'symlink':
        target.symlink_to(builtin / 'example.yaml')
    elif mode == 'dangling':
        target.symlink_to(tmp_path / 'missing')
    elif mode == 'directory':
        target.mkdir()
    elif mode == 'fifo':
        os.mkfifo(target)
    else:
        kvm.yaml_dump(target, raw())
        if mode == 'writable':
            target.chmod(0o666)
        elif os.geteuid() == 0:
            os.chown(target, 65534, 65534)
        else:
            pytest.skip('chown needs root')
    with pytest.raises(kvm.KVMError, match='Unsafe template path'):
        kvm.find_template('example')


def test_unsafe_root_or_ancestor_rejected(roots):
    local, builtin = roots
    local.chmod(0o777)
    with pytest.raises(kvm.KVMError, match='Unsafe template path'):
        kvm.find_template('example')
    local.chmod(0o755)
    local.rmdir(); local.symlink_to(builtin, target_is_directory=True)
    with pytest.raises(kvm.KVMError, match='Unsafe template path'):
        kvm.find_template('example')


def test_invalid_local_no_fallback(roots):
    (roots[0] / 'example.yaml').write_text('bad: [')
    args = kvm.build_parser().parse_args(['validate', '@example'])
    with pytest.raises(kvm.KVMError, match='Invalid YAML'):
        kvm.cmd_validate(args)


def test_relative_assets_and_saved_keys(roots, tmp_path, monkeypatch, capsys):
    local, builtin = roots
    definition = raw()
    definition['storage']['image'] = {'path': 'images/base.qcow2'}
    definition['cloud_init']['ssh_authorized_keys'] = ['file:keys/admin.pub']
    (local / 'keys').mkdir()
    (local / 'keys/admin.pub').write_text('ssh-ed25519 AAAA local\n')
    path = local / 'example.yaml'
    kvm.yaml_dump(path, definition)
    monkeypatch.chdir(tmp_path)
    before = path.read_bytes()
    args = kvm.build_parser().parse_args(['validate', '@example'])
    assert kvm.cmd_validate(args) == 0
    result = kvm.yaml.safe_load(capsys.readouterr().out)
    assert result['storage']['image']['path'] == str(local / 'images/base.qcow2')
    assert result['cloud_init']['ssh_authorized_keys'] == ['ssh-ed25519 AAAA local']
    assert path.read_bytes() == before
    (local / 'keys/admin.pub').unlink()
    assert kvm.resolve_ssh_keys(result['cloud_init']['ssh_authorized_keys'], tmp_path) == ['ssh-ed25519 AAAA local']


@pytest.mark.parametrize('file', sorted((ROOT / 'examples').glob('*.yaml')), ids=lambda p: p.name)
def test_all_shipped_templates_validate(file, roots, monkeypatch):
    local, builtin = roots
    shutil.copy2(file, builtin / file.name)
    monkeypatch.setattr(kvm, 'resolve_ssh_keys', lambda keys, directory: ['ssh-ed25519 AAAA test'])
    path, _ = kvm.find_template(file.stem)
    clone = file.name.startswith('clone-target')
    cfg = kvm.load_validated_definition(path, for_clone=clone, is_template=True)
    assert cfg['vm']['machine'] == 'q35'
    assert cfg['cloud_init']['maintenance']['profile'] == 'balanced'
    if not clone:
        assert cfg['storage']['image']['distro'] in kvm.DISTROS
    else:
        with pytest.raises(kvm.KVMError, match='For create'):
            kvm.load_validated_definition(path, is_template=True)


def test_installed_prefix_and_cli_output(roots, tmp_path):
    prefix = tmp_path / 'prefix with spaces'
    bin_dir = prefix / 'sbin'; bin_dir.mkdir(parents=True)
    executable = bin_dir / 'kvm-vm'
    shutil.copy2(ROOT / 'kvm-vm', executable)
    vendor = prefix / 'share/kvm-vm/templates'; vendor.mkdir(parents=True)
    kvm.yaml_dump(vendor / 'installed.yaml', raw('installed01'))
    env = {**os.environ, 'KVM_VM_TEMPLATE_DIR': str(roots[0])}
    env.pop('KVM_VM_BUILTIN_TEMPLATE_DIR', None)
    result = subprocess.run([sys.executable, str(executable), 'validate', '@installed'],
                            env=env, capture_output=True, text=True, check=True)
    assert kvm.yaml.safe_load(result.stdout)['vm']['name'] == 'installed01'
    assert f'Template (builtin): {vendor}/installed.yaml' in result.stderr


def test_template_dirs_independent(roots, monkeypatch):
    monkeypatch.setattr(kvm, 'DEF_DIR', roots[0])
    with pytest.raises(kvm.KVMError, match='must be distinct'):
        kvm.find_template('example')
    monkeypatch.setattr(kvm, 'DEF_DIR', Path('/etc/kvm-vm/definitions'))
    monkeypatch.setattr(kvm, 'TEMPLATE_DIR', Path('relative'))
    with pytest.raises(kvm.KVMError, match='absolute paths'):
        kvm.find_template('example')
