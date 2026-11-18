"""Offline tests for the opt-in KVM/NAT E2E lab. Never run virsh or Docker."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / 'tests/e2e'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, E2E / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


lab = module('kvm_lab_offline', 'lab.py')
ca = module('kvm_lab_ca', 'inject_lab_ca.py')


def args(tmp_path, monkeypatch):
    monkeypatch.setenv('KVM_VM_E2E_STATE_ROOT', str(tmp_path))
    return lab.build_parser().parse_args(['plan'])


def test_topology_has_five_domains_three_distinct_networks():
    d = lab.topo('abc')
    assert len(d['domains']) == 5
    assert len(d['networks']) == 3
    assert {v['mode'] for v in d['networks'].values()} == {'nat', 'isolated'}
    assert all(n.startswith('kvm-e2e-abc-') for n in [*d['networks'], *d['domains']])
    assert 'default' not in d['networks']


def test_isolated_lan_network_xml_never_forwards():
    wan = ET.fromstring(lab.net_xml('labwan', '172.30.0.1', 'nat'))
    site = ET.fromstring(lab.net_xml('labsite', '10.10.1.1', 'isolated'))
    assert wan.find('./forward').attrib['mode'] == 'nat'
    assert site.find('./forward') is None
    assert site.find('./ip').attrib['address'] == '10.10.1.1'
    assert site.find('./ip/dhcp') is None


def test_cloud_network_gateway_macs_and_no_forward_on_host():
    r = lab.cloud_network('router-a', 'cm')
    h = lab.cloud_network('host-a', 'cm')
    assert '172.30.0.11/24' in r and '10.10.1.2/24' in r
    assert 'via: 172.30.0.1' in r
    assert 'via: 10.10.1.2' in h
    assert 'wan0' in r and 'lan0' in r
    assert '10.10.2.2' not in h
    assert lab.mac_for('host-a', 'lan', 'cm') != lab.mac_for('host-b', 'lan', 'cm')


def test_label_validation_and_readonly_plan(capsys):
    assert lab.main(['--lab-id', 'lab2', 'plan']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['lab_id'] == 'lab2'
    with pytest.raises(SystemExit):
        lab.main(['--lab-id', '../../etc', 'plan'])


def test_requires_owned_manifest_and_refuses_symlink(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    with pytest.raises(lab.LabError, match='not initialized'):
        t.read_state()
    t.home.mkdir()
    t.manifest.symlink_to(tmp_path / 'other.json')
    with pytest.raises(lab.LabError, match='symbolic link'):
        t.read_state()


def test_refuses_unowned_libvirt_resources_before_initialization(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    image = tmp_path / 'cloud.img'; image.write_bytes(b'QCOW')
    with mock.patch.object(lab.os, 'geteuid', return_value=0), \
         mock.patch.object(lab.Path, 'exists', autospec=True, side_effect=lambda p: str(p) == '/dev/kvm'), \
         mock.patch.object(lab.shutil, 'which', return_value='/usr/bin/tool'), \
         mock.patch.object(lab, 'run', return_value=subprocess.CompletedProcess([], 0, 'default via 192.0.2.1', '')), \
         mock.patch.object(t, 'virsh'), \
         mock.patch.object(t, 'is_defined', return_value=True):
        # The fake cloud.img is not required to be a valid QEMU image for preflight.
        with pytest.raises(lab.LabError, match='pre-existing libvirt'):
            t.preflight(image)


def test_binary_secret_upload_uses_ssh_stdin_not_argv(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.data = {'lab_id':'cm', 'owner':'kvm-vm-e2e-lab-v1'}
    t.home.mkdir()
    called = []
    def fake_run(cmd, **kwargs):
        called.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b'', b'')
    with mock.patch.object(lab, 'run', side_effect=fake_run):
        t.scp_bytes('central', b'secret\x00value', '/root/test-secret', '0600')
    assert len(called) == 1
    command, options = called[0]
    assert 'secret\\x00value' not in ' '.join(command) and 'secret\x00value' not in ' '.join(command)
    assert options['input'] == b'secret\x00value'
    assert options['text'] is False
    assert 'sudo tee /root/test-secret' in command[-1]


def test_fault_scope_and_heal(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    called = []
    t.exec_guest = lambda node, cmd: called.append((node, cmd)) or ''
    t.ssh = lambda node, cmd: called.append((node, cmd))
    t.fault('host-b', 'disconnect')
    t.fault('host-b', 'netem', loss=3, latency=150, rate='2mbit')
    t.heal('host-b')
    assert all(node == 'router-b' for node, _ in called)
    assert 'iptables' in called[0][1]
    assert 'tc qdisc' in called[1][1]
    with pytest.raises(lab.LabError):
        t.fault('central', 'disconnect')
    with pytest.raises(lab.LabError):
        t.fault('host-b', 'netem', rate='1mbit; touch /tmp/evil')


def test_inject_private_ca_idempotent_and_keeps_password_out_of_compose(tmp_path):
    import shutil
    for name in ('compose.agent.yaml', 'compose.hybrid.yaml'):
        shutil.copy2(ROOT / 'tools/monitoring/remote' / name, tmp_path / name)
    # The real rendered file is JSON (also YAML-compatible).
    content = {'remote_write': [{'url':'https://172.30.0.10/api/v1/write',
                                 'basic_auth': {'password_file':'/etc/prometheus/remote-password'}}]}
    (tmp_path / 'prometheus-hybrid.yml').write_text(json.dumps(content))
    ca.inject(tmp_path)
    data = {p.name: p.read_text() for p in tmp_path.iterdir()}
    ca.inject(tmp_path)
    assert data == {p.name: p.read_text() for p in tmp_path.iterdir()}
    assert 'tlsCAFile=/etc/vmagent/lab-ca.pem' in data['compose.agent.yaml']
    assert './lab-ca.pem:/etc/vmagent/lab-ca.pem:ro' in data['compose.agent.yaml']
    assert './lab-ca.pem:/etc/prometheus/lab-ca.pem:ro' in data['compose.hybrid.yaml']
    assert json.loads(data['prometheus-hybrid.yml'])['remote_write'][0]['tls_config'] == {
        'ca_file': '/etc/prometheus/lab-ca.pem'}
    assert 'insecure_skip_verify' not in str(data)


def test_e2e_scenario_report_failure_is_preserved(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.home.mkdir()
    t.data = {'owner':'kvm-vm-e2e-lab-v1', 'lab_id':'cm', 'bootstrapped':True}
    t.write_state()
    t.scenario_connectivity = lambda: (_ for _ in ()).throw(lab.LabError('no reachable guest'))
    with pytest.raises(lab.LabError, match='scenarios failed'):
        t.test(('connectivity',))
    reports = list((t.home / 'reports').glob('*.json'))
    assert len(reports) == 1
    record = json.loads(reports[0].read_text())
    assert record['results'][0]['status'] == 'FAIL'
    assert 'no reachable guest' in record['results'][0]['error']


def test_command_help_and_no_real_machine_dependencies():
    call = subprocess.run([sys.executable, str(E2E/'lab.py'), '--help'],
                          text=True, capture_output=True)
    assert call.returncode == 0
    assert 'bootstrap' in call.stdout and 'fault' in call.stdout


def test_ownership_guards_refuse_replaced_domain_and_network(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.home.mkdir()
    name = lab.domain_name('cm', 'host-a')
    foreign = '<domain><devices><disk><source file="/tmp/not-ours.qcow2"/></disk></devices></domain>'
    t.virsh = lambda *a, **kw: subprocess.CompletedProcess(a, 0, foreign, '')
    with pytest.raises(lab.LabError, match='unexpected domain'):
        t.assert_owned_domain(name)
    wrong = lab.net_xml('other', '192.168.4.1', 'nat')
    t.virsh = lambda *a, **kw: subprocess.CompletedProcess(a, 0, wrong, '')
    with pytest.raises(lab.LabError, match='modified or foreign'):
        t.assert_owned_network('kvm-e2e-cm-wan', '172.30.0.1', 'nat')


def test_documented_lab_entrypoint_runs_plan():
    result = subprocess.run([str(E2E / 'lab.sh'), 'plan'], capture_output=True, check=True)
    assert len(json.loads(result.stdout)['domains']) == 5


def test_qemu_disk_directory_is_separate_from_private_state(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.data = {'separate_images': True}
    t.write_state()
    assert t.image_dir.parent == t.home.parent
    assert t.image_dir != t.home
    t.create_image_dir()
    assert t.image_dir.stat().st_mode & 0o777 == 0o711
    assert t.home.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        t.create_image_dir()
    t.data = {}  # Existing deployments keep their original cleanup path.
    assert t.image_dir == t.home


def test_debian_compose_package_installs_working_cli_plugin(tmp_path, monkeypatch):
    import os
    # Debian 13 publishes its v2 CLI plugin as docker-compose.
    stubs = {
        'sudo': '[ "$1" != DEBIAN_FRONTEND=noninteractive ] || shift\nexec "$@"',
        'apt-get': '[ "$*" = "install -y docker-compose" ] && touch "$PLUGIN"',
        'docker': '[ "$*" = "compose version" ] && test -f "$PLUGIN"',
        'systemctl': 'exit 0',
    }
    for name, body in stubs.items():
        path = tmp_path / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    monkeypatch.setenv('PLUGIN', str(tmp_path / 'plugin-installed'))
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.apt = mock.Mock()
    t.ssh = lambda node, script, timeout: subprocess.run(['bash', '-se'], input=script,
                                                           text=True, check=True)
    t.docker_packages('central', [])


@pytest.mark.parametrize('sample_timestamp,replayed', [(99, False), (135, True)])
def test_replay_requires_a_sample_recorded_during_outage(tmp_path, monkeypatch,
                                                         sample_timestamp, replayed):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.data = {'credentials': {'host-a': {'host_id': 'test'}}}
    monkeypatch.setattr(lab.time, 'time', mock.Mock(side_effect=[100, 165, 185]))
    monkeypatch.setattr(lab.time, 'sleep', lambda _: None)
    t.scenario_metrics = t.fault = t.heal = t.ssh = mock.Mock()
    t.exec_guest = mock.Mock(return_value='vmagent_remotewrite_pending_data_bytes 32')
    t.query = mock.Mock(return_value=[{}])
    # The query evaluation tick can fall within the outage for an older sample.
    t.query_range = lambda q, *args: [{'values': [[135, str(sample_timestamp)
                                                       if q.startswith('timestamp(') else '1']]}]
    t.until = lambda predicate, *args: predicate() or pytest.fail('no replayed sample')
    if replayed:
        t.scenario_disconnect_recover()
    else:
        with pytest.raises(pytest.fail.Exception, match='no replayed sample'):
            t.scenario_disconnect_recover()


def test_same_name_guests_require_headless_domains_and_running_state(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    t.data = {'credentials': {h: {'host_id': h} for h in lab.HOSTS}}
    t.ssh = mock.Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
    t.scp_bytes = mock.Mock()
    t.exec_guest = mock.Mock(return_value='shut off')
    t.until = mock.Mock()
    with pytest.raises(lab.LabError, match='running'):
        t.scenario_same_name_vms()
    xml = ET.fromstring(t.scp_bytes.call_args.args[1])
    assert xml.find('./devices/graphics') is None
    t.exec_guest.return_value = 'running'
    t.query = mock.Mock(return_value=[{'metric': {'host_id': h}} for h in lab.HOSTS])
    t.until = lambda check, *args: check() or pytest.fail('wrong hosts')
    t.scenario_same_name_vms()
    t.query.return_value = [{'metric': {'host_id': 'host-a'}}, {'metric': {'host_id': 'unrelated'}}]
    with pytest.raises(pytest.fail.Exception, match='wrong hosts'):
        t.scenario_same_name_vms()


def test_lab_https_probes_can_use_ca_in_private_config(tmp_path, monkeypatch):
    t = lab.Lab(args(tmp_path, monkeypatch))
    def request(host, cmd):
        assert cmd.startswith('sudo curl --cacert /etc/kvm-vm/monitoring/lab-ca.pem ')
        return '401'
    t.exec_guest = request
    t.ensure_connected = mock.Mock()
    t.ssh = mock.Mock(return_value=subprocess.CompletedProcess([], 1, 'blackhole 10.10.1.0/24', ''))
    t.scenario_auth()
    t.scenario_connectivity()
