"""Disk capacity presentation without a live libvirt host."""
import subprocess

from test_unit import kvm_vm

GIB = 1024 ** 3
LISTING = """ Type   Device   Target   Source
---------------------------------------------------
 file   disk     vda      /vm/system disk.qcow2
 block  disk     vdb      /dev/vg/data
 file   cdrom    sda      /vm/seed.iso
 file   cdrom    sdb      -
"""


def test_status_disks_shows_per_disk_capacity_and_preserves_sources(monkeypatch):
    commands = []
    monkeypatch.setattr(kvm_vm, 'output', lambda cmd: LISTING)

    def run(cmd, **kwargs):
        commands.append(cmd)
        assert kwargs == {'check': False, 'capture': True, 'timeout': 5}
        size = {'vda': 40, 'vdb': 100}[cmd[-1]]
        return subprocess.CompletedProcess(cmd, 0, f'Capacity:       {size * GIB}\nAllocation:     1\nPhysical:       2\n', '')

    monkeypatch.setattr(kvm_vm, 'run', run)
    rows = kvm_vm.status_disks('vm-a').splitlines()
    assert rows[0].split() == ['Type', 'Device', 'Target', 'Capacity', 'Source']
    assert rows[2].split(maxsplit=4) == ['file', 'disk', 'vda', '40.0GiB', '/vm/system disk.qcow2']
    assert rows[3].split() == ['block', 'disk', 'vdb', '100.0GiB', '/dev/vg/data']
    assert rows[4].split() == ['file', 'cdrom', 'sda', '-', '/vm/seed.iso']
    assert rows[5].split() == ['file', 'cdrom', 'sdb', '-', '-']
    assert [cmd[3:] for cmd in commands] == [['domblkinfo', 'vm-a', 'vda'], ['domblkinfo', 'vm-a', 'vdb']]


def test_status_disk_capacity_failure_is_nonfatal(monkeypatch):
    monkeypatch.setattr(kvm_vm, 'output', lambda cmd: LISTING)

    def run(cmd, **kwargs):
        if cmd[-1] == 'vda':
            raise subprocess.TimeoutExpired(cmd, 5)
        return subprocess.CompletedProcess(cmd, 1, f'Capacity: {40 * GIB}', 'unsupported')

    monkeypatch.setattr(kvm_vm, 'run', run)
    rows = kvm_vm.status_disks('vm-a').splitlines()
    assert rows[2].split(maxsplit=4)[3:] == ['-', '/vm/system disk.qcow2']
    assert rows[3].split()[3:] == ['-', '/dev/vg/data']


def test_status_empty_disks(monkeypatch):
    monkeypatch.setattr(kvm_vm, 'output', lambda cmd: ' Type Device Target Source\n---------\n')
    assert kvm_vm.status_disks('vm-a').splitlines()[0].split() == ['Type', 'Device', 'Target', 'Capacity', 'Source']


def test_status_unknown_zero_or_malformed_capacity(monkeypatch):
    monkeypatch.setattr(kvm_vm, 'output', lambda cmd: LISTING)

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, 'Capacity: 0\n' if cmd[-1] == 'vda' else 'Capacity: unknown\n', '')

    monkeypatch.setattr(kvm_vm, 'run', run)
    rows = kvm_vm.status_disks('vm-a').splitlines()
    assert rows[2].split(maxsplit=4)[3] == '-'
    assert rows[3].split(maxsplit=4)[3] == '-'
