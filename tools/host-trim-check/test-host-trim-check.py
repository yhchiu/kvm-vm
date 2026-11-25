#!/usr/bin/env python3
"""Offline tests; no real VM, FITRIM or QMP commands are executed."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('checker', Path(__file__).with_name('host-trim-check.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
XML = '''<domain><name>vm1</name><uuid>id-1</uuid><memory unit="MiB">4096</memory><vcpu current="2">4</vcpu><os><type machine="pc-i440fx-9.2">hvm</type></os><devices>
<disk type="file" device="disk"><driver type="qcow2" discard="unmap"/><source file="/a&amp;b.qcow2"/><target dev="vda" bus="virtio"/><alias name="virtio-disk0"/><backingStore/></disk>
<disk type="block" device="disk"><driver type="raw" discard="unmap"/><source dev="/dev/vg/lv"/><target dev="sda" bus="scsi"/><alias name="scsi0-0-0-0"/></disk>
<disk device="cdrom"><target dev="sdb"/></disk></devices></domain>'''
TIMER = dict(active='inactive', enabled='disabled', service={})


def vm_stub():
    v = c.parse_domain(XML)
    v.update(state='running', active=True, findings=[])
    for d in v['disks']:
        d.update(findings=[], nodes={}, leaves=[], candidates=[], complete=False, mount=None, allocation_bytes=None)
    return v


class CheckerTest(unittest.TestCase):
    def test_xml_and_machine(self):
        v = c.parse_domain(XML)
        self.assertEqual(v['machine'], 'pc-i440fx-9.2')
        self.assertEqual(v['disks'][0]['source'], '/a&b.qcow2')
        self.assertEqual(v['vcpus'], 2)
        self.assertEqual(v['max_memory_bytes'], 4096 * 2**20)
        self.assertEqual(len(v['disks']), 2)
        self.assertFalse(v['disks'][0]['backing'])

    def test_changed_xml(self):
        self.assertNotEqual(c.config_signature(c.parse_domain(XML)), c.config_signature(c.parse_domain(XML.replace('discard="unmap"', 'discard="ignore"'))))

    def test_thin_policy(self):
        pool = dict(vg_name='vg', lv_name='pool', segtype='thin-pool', discards='passdown')
        thin = dict(vg_name='vg', lv_name='lv', segtype='thin', pool_lv='pool')
        self.assertEqual(c.thin_policy(thin, [pool, thin]), pool)
        self.assertIsNone(c.thin_policy(thin, [thin]))

    def test_counter_reset(self):
        self.assertEqual(c.delta(('8:0','1',4,12), ('8:0','1',8,32)), (4,20))
        self.assertIsNone(c.delta(('8:0','1',4,12), ('8:0','2',8,32)))
        self.assertIsNone(c.delta(('8:0','1',4,12), ('8:0','1',3,32)))

    def test_qmp_alias_and_nodes(self):
        row = dict(qdev='/machine/peripheral/virtio-disk0/virtio-backend', **{'node-name':'format'}, stats={'unmap_operations':5,'unmap_bytes':100}, parent={'node-name':'storage','stats':{'unmap_operations':2,'unmap_bytes':50}})
        self.assertEqual(c.match_stats([row], 'virtio-disk0'), row)
        self.assertIsNone(c.match_stats([row,row], 'virtio-disk0'))
        self.assertIsNone(c.match_stats([row], 'virtio-disk'))
        chain = c.stats_chain(row)
        self.assertEqual(len(chain), 2)
        self.assertEqual(c.qmp_delta(chain, chain)[0]['operations'], 0)
        other = [dict(chain[0], node='other')]
        self.assertEqual(c.qmp_delta(chain, other), [])

    def test_qmp_feature_false(self):
        v = vm_stub()
        with patch.object(c,'qmp',return_value=False):
            c.qom_feature([],v,v['disks'][0])
        self.assertEqual(v['disks'][0]['findings'][-1]['status'], 'FAIL')

    def test_graph(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td); disk=base/'devices'/'nvme0n1'; part=disk/'nvme0n1p1'; dm=base/'devices'/'dm-0'; sys=base/'sys'; sys.mkdir()
            for p in (disk,part,dm):
                (p/'slaves').mkdir(parents=True,exist_ok=True)
                (sys/p.name).symlink_to(p)
            (part/'partition').touch()
            (dm/'slaves'/part.name).symlink_to(part)
            with patch.object(c,'SYS',sys):
                nodes, leaves, missing=c.graph('dm-0')
            self.assertEqual(nodes, {'dm-0':['nvme0n1p1'],'nvme0n1p1':['nvme0n1'],'nvme0n1':[]})
            self.assertEqual(leaves, {'nvme0n1'}); self.assertFalse(missing)

    def test_snapshot_filtered_disk(self):
        v=vm_stub(); v['_full_signature']=c.config_signature(v); v['disks']=v['disks'][:1]
        with patch.object(c,'run',side_effect=[XML,'running']), patch.object(c,'process_identity',return_value=('1','2')), patch.object(c,'qmp_stats',return_value={}):
            self.assertTrue(c.snapshot([],v)['valid'])

    def test_phase_observed_and_reset(self):
        v=vm_stub(); v['disks']=v['disks'][:1]; d=v['disks'][0]; d.update(candidates=['nvme0n1'],complete=True)
        def snap(n, alloc, identity=('1','2')):
            return dict(valid=True,identity=identity,allocations={'vda':(1,2,alloc,10)},block={'nvme0n1':('1:1','1',n,n)},qmp={'vda':[dict(node='n',operations=n,bytes=n,failed=0,invalid=0)]})
        a,b=snap(2,100),snap(4,50)
        self.assertIn('CORRELATED',c.phase_report(v,a,b,'guest')['disks'][0]['findings'][0]['message'])
        self.assertEqual(c.phase_report(v,a,snap(4,50,('2','2')),'guest')['findings'][0]['status'],'UNKNOWN')
        b['allocations']['vda']=(1,3,50,10)
        self.assertEqual(c.phase_report(v,a,b,'guest')['disks'][0]['findings'][0]['status'],'UNKNOWN')

    def test_host_phase_only_selected_mount(self):
        v=vm_stub(); v['disks'][0]['mount']={'target':'/data'}
        s=dict(valid=True,identity=('1','2'),allocations={'vda':(1,2,10,10)},block={},qmp={})
        self.assertEqual([d['target'] for d in c.phase_report(v,s,s,'host','/data')['disks']],['vda'])

    def test_static_json_and_strict_exit(self):
        v=vm_stub(); c.finding(v,'UNKNOWN','mock')
        def command(args,timeout=30):
            if 'list' in args: return 'vm1\n'
            if 'domuuid' in args: return 'id-1'
            raise AssertionError(args)
        out=io.StringIO()
        with patch.object(c.shutil,'which',return_value='/bin/mock'), patch.object(c,'timer_info',return_value=TIMER), patch.object(c,'lvm_info',return_value=[]), patch.object(c,'run',side_effect=command), patch.object(c,'inventory',return_value=v), contextlib.redirect_stdout(out):
            rc=c.main(['vm1','--disk','vda','--no-qmp','--json','--strict'])
        data=json.loads(out.getvalue()); self.assertEqual(rc,3); self.assertEqual(data['exit_code'],3); self.assertEqual(len(data['vms'][0]['disks']),1)

    def test_invalid_runtime_cli(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            c.main(['--guest-trim'])
        self.assertEqual(cm.exception.code,2)

    def test_guest_failure_skips_host(self):
        v=vm_stub(); s=dict(valid=True,identity=('1','2'),allocations={},block={},qmp={})
        r=dict(phases=[],operation_error=False,findings=[])
        args=type('A',(),dict(guest_trim=True,mountpoint=None,timeout=1,watch=None,settle=0,host_trim=True))()
        with patch.object(c,'snapshot',return_value=s), patch.object(c,'run',side_effect=RuntimeError('guest unavailable')) as run, contextlib.redirect_stderr(io.StringIO()):
            c.active_test([],v,args,r)
        self.assertTrue(r['operation_error']); self.assertEqual(run.call_count,1); self.assertNotIn('host_trim',r)

    def test_mount_policy_online_no_timer(self):
        d=c.parse_domain(XML)['disks'][0]
        m={'target':'/data','fstype':'ext4','source':'/dev/mock','options':'rw,discard','maj:min':'1:1'}
        with patch.object(c.os,'stat'), patch.object(c,'allocation',return_value=(1,2,10,20)), patch.object(c,'mount_info',return_value=m), patch.object(c,'block_name',return_value='mock'), patch.object(c,'graph',return_value=({'mock':[]},{'mock'},set())), patch.object(c,'endpoint',return_value=True), patch.object(c,'queue',return_value=100), patch.object(c,'counters',return_value=None), patch.object(c,'read',return_value=None):
            c.inspect_disk(d,TIMER,[])
        self.assertTrue(any('online discard' in f['message'] and f['status']=='OK' for f in d['findings']))
        self.assertFalse(any(f['status']=='FAIL' for f in d['findings']))


if __name__=='__main__':
    unittest.main(verbosity=2)
