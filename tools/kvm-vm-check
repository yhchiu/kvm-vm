#!/usr/bin/env python3
"""Read-only KVM host audit and opt-in layered TRIM diagnostics."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

SYS = Path('/sys/class/block')
LOCAL_FS = {'ext4', 'ext3', 'xfs', 'f2fs'}


def run(args, timeout=30):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, timeout=timeout, check=False, env=dict(os.environ, LC_ALL='C'))
    if p.returncode:
        raise RuntimeError(f"{args[0]}: {p.stderr.strip() or p.stdout.strip() or p.returncode}")
    return p.stdout


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def amount(n):
    return f'{n:,} B ({n / 1048576:,.2f} MiB)'


def allocation(path):
    s = os.stat(path)
    if not stat.S_ISREG(s.st_mode):
        raise RuntimeError('source is not a regular file')
    return (s.st_dev, s.st_ino, s.st_blocks * 512, s.st_size)


def mount_info(path):
    data = json.loads(run(['findmnt', '--json', '--target', path, '--output',
                           'TARGET,SOURCE,FSTYPE,OPTIONS,MAJ:MIN']))
    rows = data.get('filesystems', [])
    if len(rows) != 1:
        raise RuntimeError('mount resolution is ambiguous')
    return rows[0]


def block_name(major_minor):
    if not major_minor or not re.fullmatch(r'\d+:\d+', major_minor):
        return None
    p = Path('/sys/dev/block') / major_minor
    return p.resolve().name if p.exists() else None


def graph(start):
    """Return nodes and terminal whole devices; partition parent is also tracked."""
    nodes, leaves, missing = {}, set(), set()
    def visit(name):
        if name in nodes:
            return
        p = SYS / name
        if not p.exists():
            missing.add(name)
            return
        deps = set()
        if (p / 'partition').exists():
            deps.add(p.resolve().parent.name)
        try:
            deps.update(x.name for x in (p / 'slaves').iterdir())
        except OSError:
            if not (p / 'partition').exists():
                missing.add(name)
        nodes[name] = sorted(deps)
        if not deps:
            leaves.add(name)
        for dep in sorted(deps):
            visit(dep)
    if start:
        visit(start)
    return nodes, leaves, missing


def queue(name, attr):
    p = SYS / name
    value = read(p / 'queue' / attr)
    if value is None and (p / 'partition').exists():
        value = read(p.resolve().parent / 'queue' / attr)
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def endpoint(name):
    """A candidate SSD as exposed to this host, not proof of physical hardware."""
    p = SYS / name
    if name.startswith(('loop', 'ram', 'zram', 'dm-', 'md', 'nbd', 'rbd', 'zd')):
        return False
    return (p / 'device').exists() and queue(name, 'rotational') == 0


def counters(name):
    # Linux block stat fields 12 and 14 (1-based); sectors are always 512 B.
    p = SYS / name
    try:
        fields = (p / 'stat').read_text().split()
        if len(fields) < 15:
            return None
        return (read(p / 'dev'), read(p / 'diskseq'), int(fields[11]), int(fields[13]))
    except (OSError, ValueError):
        return None


def delta(a, b):
    if a is None or b is None or a[:2] != b[:2] or a[2] > b[2] or a[3] > b[3]:
        return None
    return b[2] - a[2], b[3] - a[3]


VERSION = '1.0.0'
ERRORS = (RuntimeError, OSError, ValueError, ET.ParseError, subprocess.TimeoutExpired)
STATES = ('OK', 'OBSERVED', 'WARN', 'FAIL', 'UNKNOWN', 'SKIPPED')


def finding(obj, status, message):
    obj.setdefault('findings', []).append({'status': status, 'message': message})


def optional(cmd, timeout=15):
    try:
        return run(cmd, timeout).strip()
    except ERRORS:
        return None


def parse_domain(xml):
    root = ET.fromstring(xml)
    disks = []
    for d in root.findall('./devices/disk'):
        if d.get('device') != 'disk':
            continue
        drv, src, tgt, alias = (d.find(x) for x in ('driver', 'source', 'target', 'alias'))
        if tgt is None:
            continue
        driver = drv.attrib if drv is not None else {}
        source = src.attrib if src is not None else {}
        backing = d.find('backingStore')
        disks.append(dict(target=tgt.get('dev'), bus=tgt.get('bus'),
                          alias=alias.get('name') if alias is not None else None,
                          kind=d.get('type'), format=driver.get('type'),
                          source=source.get('file') or source.get('dev'),
                          source_attributes=source, discard=driver.get('discard'),
                          detect_zeroes=driver.get('detect_zeroes'),
                          discard_no_unref=driver.get('discard_no_unref'),
                          cache=driver.get('cache'), readonly=d.find('readonly') is not None,
                          backing=bool(backing is not None and (len(backing) or backing.attrib)),
                          external_data=d.find('.//dataStore') is not None))
    os_type = root.find('./os/type')
    memory = root.find('memory')
    units = {'b': 1, 'bytes': 1, 'kib': 1024, 'mib': 2**20, 'gib': 2**30,
             'tib': 2**40, 'kb': 1000, 'mb': 10**6, 'gb': 10**9, 'tb': 10**12,
             'k': 1024, 'm': 2**20, 'g': 2**30, 't': 2**40}
    mem = None
    if memory is not None:
        factor = units.get(memory.get('unit', 'KiB').lower())
        if factor is not None:
            mem = int(memory.text) * factor
    vcpu = root.find('vcpu')
    return dict(name=root.findtext('name'), uuid=root.findtext('uuid'),
                machine=os_type.get('machine') if os_type is not None else None,
                vcpus=int(vcpu.get('current', vcpu.text)) if vcpu is not None else None,
                max_memory_bytes=mem, disks=disks)


def disk_signature(d):
    return tuple(d.get(k) for k in ('target', 'bus', 'kind', 'format', 'source', 'discard',
                                    'detect_zeroes', 'discard_no_unref', 'cache', 'readonly', 'external_data', 'backing')) + (tuple(sorted(d.get('source_attributes', {}).items())),)


def config_signature(vm):
    return vm['machine'], sorted((disk_signature(d) for d in vm['disks']), key=lambda x: x[0] or '')


def timer_info():
    if not shutil.which('systemctl'):
        return {'active': None, 'enabled': None, 'service': {}}
    result = {'active': optional(['systemctl', 'is-active', 'fstrim.timer']),
              'enabled': optional(['systemctl', 'is-enabled', 'fstrim.timer'])}
    # systemctl is-active returns nonzero for inactive; show provides actual state.
    for unit, keys, dest in [('fstrim.timer', 'ActiveState,UnitFileState,LastTriggerUSec', 'timer'),
                             ('fstrim.service', 'Result,ExecMainStatus,ExecMainExitTimestamp,ExecStart', 'service')]:
        output = optional(['systemctl', 'show', unit, '--property=' + keys])
        result[dest] = dict(line.split('=', 1) for line in (output or '').splitlines() if '=' in line)
    result['active'] = result['timer'].get('ActiveState', result['active'])
    result['enabled'] = result['timer'].get('UnitFileState', result['enabled'])
    return result


def lvm_info():
    if not shutil.which('lvs'):
        return None
    try:
        data = json.loads(run(['lvs', '-a', '--reportformat', 'json', '-o',
                               'vg_name,lv_name,segtype,pool_lv,discards,data_percent,metadata_percent,lv_kernel_major,lv_kernel_minor']))
        return [r for report in data['report'] for r in report.get('lv', [])]
    except (KeyError, TypeError) + ERRORS:
        return None


def thin_policy(row, lvs):
    if row.get('segtype') == 'thin-pool':
        return row
    if row.get('segtype') == 'thin':
        return next((p for p in lvs if p.get('vg_name') == row.get('vg_name') and
                     p.get('lv_name') == row.get('pool_lv') and p.get('segtype') == 'thin-pool'), None)
    return None


def inspect_disk(d, timer, lvs):
    d.update(findings=[], mount=None, nodes={}, leaves=[], candidates=[], complete=False, allocation_bytes=None)
    if d['readonly']:
        finding(d, 'SKIPPED', 'Read-only disk; writable discard path is not applicable.')
        return
    if d['discard'] == 'unmap':
        finding(d, 'OK', 'XML permits discard forwarding; not runtime proof.')
    elif d['discard'] in (None, 'ignore'):
        finding(d, 'FAIL', 'XML discard is ignore/default; QEMU forwarding is not enabled explicitly.')
    else:
        finding(d, 'UNKNOWN', 'Unrecognized discard mode.')
    if d['backing'] or d['discard_no_unref'] == 'yes' or d['external_data']:
        finding(d, 'WARN', 'Backing chain, external data or discard_no_unref may affect allocation or attribution.')
    if d['kind'] not in ('file', 'block') or d['format'] not in ('qcow2', 'raw') or not d['source']:
        finding(d, 'UNKNOWN', 'Only local file/block raw or qcow2 disks are supported.')
        return
    try:
        d['path'] = os.path.realpath(d['source'])
        s = os.stat(d['path'])
        if d['kind'] == 'file':
            a = allocation(d['path'])
            d['allocation_bytes'], d['apparent_bytes'] = a[2:]
            m = mount_info(d['path'])
            d['mount'] = m
            if m.get('fstype') not in LOCAL_FS or d['external_data']:
                finding(d, 'UNKNOWN', 'Filesystem/external data topology not supported.')
                return
            start = block_name(m.get('maj:min'))
        else:
            if not stat.S_ISBLK(s.st_mode):
                raise RuntimeError('source is not a block device')
            start = block_name(f'{os.major(s.st_rdev)}:{os.minor(s.st_rdev)}')
        nodes, leaves, missing = graph(start)
        d.update(start=start, leaves=sorted(leaves), candidates=sorted(n for n in leaves if endpoint(n)),
                 complete=bool(start and leaves and not missing))
        if not d['complete']:
            finding(d, 'UNKNOWN', 'Incomplete block topology.')
        seen_pools = set()
        for name, deps in nodes.items():
            p = SYS / name
            c = counters(name)
            n = dict(dependencies=deps, rotational=queue(name, 'rotational'),
                     discard_max_bytes=queue(name, 'discard_max_bytes'), model=read(p / 'device/model'),
                     discard_operations=c[2] if c else None, discarded_bytes=c[3]*512 if c else None,
                     md_level=read(p / 'md/level'), dm_uuid=read(p / 'dm/uuid'))
            d['nodes'][name] = n
            if c is None:
                finding(d, 'UNKNOWN', f'{name}: cumulative discard counters unavailable.')
            if n['discard_max_bytes'] is None:
                finding(d, 'UNKNOWN', f'{name}: discard capability unavailable.')
            if n['md_level'] in ('raid5', 'raid6'):
                n['raid456_discard_safely'] = read(Path('/sys/module/raid456/parameters/devices_handle_discard_safely'))
                finding(d, 'WARN', f'{name}: MD {n["md_level"]}; inspect queue capability and kernel policy; do not force-enable discard.')
            if (n['dm_uuid'] or '').startswith('LVM-'):
                dev = read(p / 'dev')
                rows = [r for r in (lvs or []) if f'{r.get("lv_kernel_major")}:{r.get("lv_kernel_minor")}' == dev]
                if not rows:
                    finding(d, 'UNKNOWN', f'{name}: LVM discard policy unavailable.')
                for r in rows:
                    pool = thin_policy(r, lvs)
                    n['lvm_segtype'] = r.get('segtype')
                    if r.get('segtype') in ('thin', 'thin-pool') and pool is None:
                        finding(d, 'UNKNOWN', f'{name}: thin pool not resolved.')
                    if pool is None:
                        continue
                    n['thin_pool'] = pool
                    key = (pool.get('vg_name'), pool.get('lv_name'))
                    if key in seen_pools:
                        continue
                    seen_pools.add(key)
                    mode = pool.get('discards')
                    status = 'OK' if mode == 'passdown' else 'FAIL' if mode in ('ignore', 'nopassdown') else 'UNKNOWN'
                    finding(d, status, f'Thin pool {key[0]}/{key[1]}: discards={mode}; configured policy, not proof of runtime passdown.')
                    for attr in ('data_percent', 'metadata_percent'):
                        try:
                            if float(pool.get(attr, '')) >= 90:
                                finding(d, 'WARN', f'Thin pool {key[1]} {attr}={pool[attr]}% (capacity risk).')
                        except (ValueError, TypeError):
                            pass
        if not d['candidates']:
            finding(d, 'UNKNOWN', 'No identifiable nonrotational SSD candidate; HDD, RAID controller or virtual storage may obscure hardware.')
        else:
            # Dependency graph can contain metadata/mirror branches, not all are data paths.
            for name, n in d['nodes'].items():
                if n['discard_max_bytes'] == 0:
                    status = 'FAIL' if name == start else 'WARN'
                    finding(d, status, f'{name}: discard_max_bytes=0; ' + ('source block device does not accept discard.' if name == start else 'branch does not advertise discard; may be a metadata branch.'))
            if d['complete'] and all(n['discard_max_bytes'] is not None and n['discard_max_bytes'] > 0 for n in d['nodes'].values()):
                finding(d, 'OK', 'All discovered block queues advertise discard; actual forwarding not established.')
        if d['mount']:
            opts = (d['mount'].get('options') or '').split(',')
            online = any(o == 'discard' or o in ('discard=async', 'discard=sync') for o in opts)
            if online:
                finding(d, 'OK', 'Host mount explicitly enables online discard.')
            elif timer['active'] == 'active':
                finding(d, 'OK', 'Host fstrim.timer active; mount inclusion and successful execution not proven.')
                if timer['service'].get('Result') not in (None, '', 'success'):
                    finding(d, 'WARN', 'Last fstrim.service result is not success.')
            else:
                finding(d, 'WARN', 'No explicit online discard or active fstrim.timer detected; other schedules/manual trim may exist.')
            if 'ro' in opts:
                finding(d, 'WARN', 'Host mount is read-only.')
        if any(n.startswith(('loop', 'nbd', 'rbd', 'zram')) for n in leaves):
            d['complete'] = False
            finding(d, 'UNKNOWN', 'Loop/remote/virtual block endpoint; underlying physical path not resolved.')
    except ERRORS as exc:
        finding(d, 'UNKNOWN', str(exc))


def qmp(virsh, vm, command, arguments=None):
    payload = {'execute': command}
    if arguments is not None:
        payload['arguments'] = arguments
    result = json.loads(run(virsh + ['qemu-monitor-command', vm, json.dumps(payload)], 15))
    if 'error' in result or 'return' not in result:
        raise RuntimeError(f'QMP {command}: {result.get("error", "no return value")}')
    return result['return']


def match_stats(rows, alias):
    if not alias:
        return None
    matches = [r for r in rows if r.get('qdev') == alias or alias in (r.get('qdev') or '').split('/')
               or r.get('device') in (alias, 'drive-' + alias)]
    return matches[0] if len(matches) == 1 else None


def stats_chain(row):
    chain = []
    while isinstance(row, dict):
        s = row.get('stats', {})
        chain.append(dict(node=row.get('node-name'), device=row.get('device'), qdev=row.get('qdev'),
                          operations=s.get('unmap_operations'), bytes=s.get('unmap_bytes'),
                          failed=s.get('failed_unmap_operations'), invalid=s.get('invalid_unmap_operations')))
        row = row.get('parent')
    return chain


def qmp_stats(virsh, vm):
    try:
        rows = qmp(virsh, vm['uuid'], 'query-blockstats')
        return {d['target']: stats_chain(match_stats(rows, d['alias'])) for d in vm['disks']}
    except ERRORS:
        return {}


def qom_feature(virsh, vm, d):
    if d['bus'] != 'virtio' or not d['alias']:
        finding(d, 'SKIPPED', 'VirtIO-BLK QOM discard property check not applicable.')
        return
    path = '/machine/peripheral/' + d['alias']
    for candidate in (path, path + '/virtio-backend'):
        try:
            val = qmp(virsh, vm['uuid'], 'qom-get', {'path': candidate, 'property': 'discard'})
        except ERRORS:
            continue
        if isinstance(val, bool):
            d['qom_discard'] = val
            finding(d, 'OK' if val else 'FAIL', f'QOM VirtIO-BLK discard property={val}; guest negotiation not verified.')
            return
    finding(d, 'UNKNOWN', 'VirtIO-BLK runtime discard property unavailable.')


def process_identity(vm):
    # Linux libvirt system daemon pidfile; absent is explicitly reported.
    name = vm['name']
    if not name or '/' in name or name in ('.', '..'):
        return None
    for base in ('/run/libvirt/qemu', '/var/run/libvirt/qemu'):
        pid = read(Path(base) / (name + '.pid'))
        if pid and pid.isdigit():
            data = read(Path('/proc') / pid / 'stat')
            if data and ')' in data:
                fields = data.rsplit(')', 1)[1].split()
                if len(fields) > 19:
                    return (pid, fields[19])
    return None


def inventory(virsh, name, timer, lvs):
    vm = parse_domain(run(virsh + ['dumpxml', name]))
    if not vm['uuid']:
        raise RuntimeError('Missing domain UUID')
    vm['findings'] = []
    vm['state'] = run(virsh + ['domstate', vm['uuid']]).strip()
    vm['active'] = vm['state'] in ('running', 'paused', 'blocked', 'pmsuspended', 'in shutdown', 'crashed', 'idle')
    if vm['active']:
        try:
            saved = parse_domain(run(virsh + ['dumpxml', vm['uuid'], '--inactive']))
            if config_signature(saved) != config_signature(vm):
                finding(vm, 'WARN', 'Live and persistent machine/disk settings differ; investigate before planned restart.')
            else:
                finding(vm, 'OK', 'Live and persistent inspected settings match.')
        except ERRORS:
            finding(vm, 'UNKNOWN', 'Persistent XML unavailable (possibly transient domain).')
    for d in vm['disks']:
        inspect_disk(d, timer, lvs)
    return vm


def snapshot(virsh, vm):
    snap = dict(identity=process_identity(vm), allocations={}, block={}, qmp={}, valid=False)
    try:
        current = parse_domain(run(virsh + ['dumpxml', vm['uuid']]))
        if run(virsh + ['domstate', vm['uuid']]).strip() != 'running' or config_signature(current) != vm.get('_full_signature', config_signature(vm)):
            raise RuntimeError('VM state or disk configuration changed')
        snap['valid'] = True
    except ERRORS as exc:
        snap['error'] = str(exc)
        return snap
    for d in vm['disks']:
        if d.get('path') and os.path.realpath(d['source']) != d['path']:
            snap.update(valid=False, error='source symlink changed')
            return snap
        if d.get('kind') == 'file' and d.get('path'):
            try:
                snap['allocations'][d['target']] = allocation(d['path'])
            except ERRORS:
                pass
        if d['nodes']:
            try:
                if d['kind'] == 'file':
                    m = mount_info(d['path'])
                    if any(m.get(k) != d['mount'].get(k) for k in ('target', 'maj:min', 'source', 'fstype', 'options')):
                        raise RuntimeError('mount identity changed')
                    start = block_name(m.get('maj:min'))
                else:
                    st = os.stat(d['path'])
                    start = block_name(f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}') if stat.S_ISBLK(st.st_mode) else None
                nodes, _, missing = graph(start)
                if missing or nodes != {n: data['dependencies'] for n, data in d['nodes'].items()}:
                    raise RuntimeError('block topology changed or incomplete')
            except ERRORS as exc:
                snap.update(valid=False, error=str(exc))
                return snap
        for name in d['nodes']:
            snap['block'][name] = counters(name)
    snap['qmp'] = qmp_stats(virsh, vm)
    return snap


def qmp_delta(a, b):
    result = []
    for x, y in zip(a or [], b or []):
        ident = ('node', 'device', 'qdev')
        if not any(x.get(k) for k in ident) or any(x.get(k) != y.get(k) for k in ident):
            break
        vals = {}
        for key in ('operations', 'bytes', 'failed', 'invalid'):
            u, v = x.get(key), y.get(key)
            vals[key] = v-u if isinstance(u, int) and isinstance(v, int) and v >= u else None
        result.append(dict(node=x.get('node'), **vals))
    return result


def phase_report(vm, a, b, phase, mount=None):
    p = dict(phase=phase, findings=[], disks=[], block={})
    if not a['valid'] or not b['valid'] or (a['identity'] and b['identity'] and a['identity'] != b['identity']):
        finding(p, 'UNKNOWN', 'VM state, disk configuration or process changed; discard deltas invalid.')
        return p
    if not a['identity'] or not b['identity']:
        finding(p, 'WARN', 'QEMU process continuity could not be verified.')
    for name in sorted(a['block']):
        diff = delta(a['block'][name], b['block'].get(name))
        p['block'][name] = dict(operations=diff[0], bytes=diff[1]*512) if diff is not None else None
    for d in vm['disks']:
        if phase == 'host' and (d.get('mount') or {}).get('target') != mount:
            continue
        t = d['target']
        x, y = a['allocations'].get(t), b['allocations'].get(t)
        freed = x[2]-y[2] if x and y and x[:2] == y[:2] else None
        if d['readonly']:
            continue
        if d['kind'] == 'file' and (not x or not y or x[:2] != y[:2]):
            r = dict(target=t, net_reclaimed_bytes=None, qmp_chain_delta=[], observed_ssd=[], findings=[])
            finding(r, 'UNKNOWN', 'File identity or allocation unavailable/changed; per-disk observation invalid.')
            p['disks'].append(r)
            continue
        chain = qmp_delta(a['qmp'].get(t), b['qmp'].get(t))
        frontend = chain[0] if chain else {}
        qseen = (frontend.get('operations') or 0) > 0 and (frontend.get('bytes') or 0) > 0
        endpoints = [n for n in d['candidates'] if p['block'].get(n) and p['block'][n]['operations'] > 0 and p['block'][n]['bytes'] > 0]
        r = dict(target=t, net_reclaimed_bytes=freed, qmp_chain_delta=chain, observed_ssd=endpoints, findings=[])
        p['disks'].append(r)
        if phase == 'host':
            message = 'HOST_DISCARD_OBSERVED: device-wide discard during host FITRIM; not VM-specific.' if endpoints else 'INCONCLUSIVE: no SSD discard observed during host phase.'
        elif qseen and endpoints:
            message = 'CORRELATED: QEMU per-disk unmap and SSD discard increased; attribution not proven.'
        elif freed is not None and freed > 0 and endpoints:
            message = 'CORRELATED: image allocation shrank and SSD discard increased; QEMU evidence unavailable/absent; attribution not proven.'
        elif qseen:
            message = 'QEMU_UNMAP_OBSERVED: no SSD discard observed; host periodic trim or other layers may explain this.'
        elif freed is not None and freed > 0:
            message = 'IMAGE_RECLAIM_OBSERVED: allocation shrank; source not proven.'
        elif endpoints:
            message = 'SSD_DISCARD_OBSERVED: device-wide discard; VM origin unknown.'
        else:
            message = 'INCONCLUSIVE: no positive correlated evidence.'
        finding(r, 'OBSERVED' if endpoints or qseen or (freed is not None and freed > 0) else 'UNKNOWN', message)
        if not chain or frontend.get('operations') is None or frontend.get('bytes') is None:
            finding(r, 'UNKNOWN', 'Per-disk QMP unmap counters unavailable or reset.')
        if not d['complete'] or not d['candidates'] or any(p['block'].get(n) is None for n in d['candidates']):
            finding(r, 'UNKNOWN', 'SSD endpoint coverage or counters incomplete.')
        if (frontend.get('failed') or 0) > 0 or (frontend.get('invalid') or 0) > 0:
            finding(r, 'WARN', 'QEMU failed/invalid unmap operations increased.')
    return p


def host_info(vms, timer, args):
    h = dict(hostname=os.uname().nodename, timer=timer, findings=[],
             cpu_threads=os.cpu_count(), running_vcpus=None, max_guest_memory_bytes=None)
    mem = read(Path('/proc/meminfo')) or ''
    for key, dest in [('MemTotal', 'memory_bytes'), ('MemAvailable', 'available_memory_bytes')]:
        m = re.search(r'^' + key + r':\s+(\d+)\s+kB', mem, re.M)
        h[dest] = int(m.group(1))*1024 if m else None
    active = [v for v in vms if v.get('active')]
    if any('state' not in v for v in vms):
        finding(h, 'UNKNOWN', 'Host resource totals and shared disk mapping may be incomplete; some VMs could not be inspected.')
        return h
    if all(v.get('vcpus') is not None for v in active):
        h['running_vcpus'] = sum(v['vcpus'] for v in active)
        if h['cpu_threads'] and h['running_vcpus'] > h['cpu_threads']:
            finding(h, 'WARN', 'vCPU overcommit detected; not necessarily a performance problem.')
    if all(v.get('max_memory_bytes') is not None for v in active):
        h['max_guest_memory_bytes'] = sum(v['max_memory_bytes'] for v in active)
        if h['memory_bytes'] is not None:
            h['nominal_headroom_bytes'] = h['memory_bytes'] - h['max_guest_memory_bytes']
            if args.min_host_memory_gib is not None and h['nominal_headroom_bytes'] < args.min_host_memory_gib * 2**30:
                finding(h, 'WARN', 'Nominal memory headroom below configured policy; not measured actual usage.')
    return h


def progress(message):
    print(message, file=sys.stderr, flush=True)


def active_test(virsh, vm, args, report):
    phases = report['phases']
    if vm['state'] != 'running':
        finding(report, 'FAIL', 'Runtime observation requires a running VM.')
        report['operation_error'] = True
        return
    before = snapshot(virsh, vm)
    if not before['valid']:
        finding(report, 'UNKNOWN', 'Baseline unavailable: ' + before.get('error', 'unknown error'))
        report['operation_error'] = True
        return
    if args.guest_trim:
        progress('Requesting Guest FITRIM; --disk filters observations, not the Guest Agent operation.')
        cmd = virsh + ['domfstrim', vm['uuid']]
        if args.mountpoint:
            cmd += ['--mountpoint', args.mountpoint]
        try:
            report['guest_trim'] = {'success': True, 'output': run(cmd, args.timeout).strip()}
        except ERRORS as exc:
            report['guest_trim'] = {'success': False, 'error': str(exc)}
            report['operation_error'] = True
            finding(report, 'FAIL', 'Guest FITRIM failed or timed out; operation may still be running.')
    seconds = args.watch if args.watch is not None else args.settle
    progress(f'Observing for {seconds}s' + ('; execute fstrim in the guest now.' if not args.guest_trim else ' after Guest FITRIM.'))
    time.sleep(seconds)
    after = snapshot(virsh, vm)
    phases.append(phase_report(vm, before, after, 'guest'))
    if not after['valid'] or (before['identity'] and after['identity'] and before['identity'] != after['identity']):
        report['operation_error'] = True
    if not args.host_trim:
        return
    if report['operation_error'] or not after['valid'] or (before['identity'] and after['identity'] and before['identity'] != after['identity']):
        finding(report, 'SKIPPED', 'Host FITRIM skipped after guest operation failure or VM identity change.')
        report['operation_error'] = True
        return
    # Revalidate each source and mount identity immediately before whole-filesystem FITRIM.
    mounts = {}
    for d in vm['disks']:
        m = d.get('mount')
        if not m or not d['complete'] or not d['candidates'] or d['readonly']:
            continue
        if 'ro' in (m.get('options') or '').split(','):
            continue
        a = after['allocations'].get(d['target'])
        initial = before['allocations'].get(d['target'])
        if a and initial and a[:2] == initial[:2]:
            mounts.setdefault(m['target'], []).append((d, m, a))
    if not mounts:
        finding(report, 'FAIL', 'No eligible local host filesystem for FITRIM (raw block disks do not require host FITRIM).')
        report['operation_error'] = True
        return
    progress('Host FITRIM will trim entire selected host filesystems, including unrelated free space.')
    report['host_trim'] = []
    for mount, sources in sorted(mounts.items()):
        host_before = snapshot(virsh, vm)
        try:
            if not host_before['valid'] or (after['identity'] and host_before['identity'] and after['identity'] != host_before['identity']):
                raise RuntimeError('VM changed before Host FITRIM')
            for d, m, a in sources:
                current = mount_info(d['path'])
                if allocation(d['path'])[:2] != a[:2] or any(current.get(k) != m.get(k) for k in ('target', 'maj:min', 'source', 'fstype', 'options')):
                    raise RuntimeError('source or mount identity changed')
            output = run(['fstrim', '--verbose', '--', mount], args.timeout).strip()
            report['host_trim'].append({'mount': mount, 'success': True, 'output': output})
        except ERRORS as exc:
            report['host_trim'].append({'mount': mount, 'success': False, 'error': str(exc)})
            report['operation_error'] = True
            finding(report, 'FAIL', f'Host FITRIM failed/skipped on {mount}: {exc}')
        time.sleep(args.settle)
        p = phase_report(vm, host_before, snapshot(virsh, vm), 'host', mount)
        p['mount'] = mount
        phases.append(p)
        if report['operation_error']:
            break


def all_findings(obj):
    if isinstance(obj, dict):
        yield from obj.get('findings', [])
        for key, val in obj.items():
            if key != 'findings':
                yield from all_findings(val)
    elif isinstance(obj, list):
        for item in obj:
            yield from all_findings(item)


def show_findings(obj, indent='  '):
    for f in obj.get('findings', []):
        print(f'{indent}{f["status"]:8} {f["message"]}')


def show_report(r):
    h = r['host']
    print(f'KVM host: {h["hostname"]} | v{VERSION} | {r["mode"]}')
    print(f'Host fstrim.timer: {h["timer"]["active"]}; enabled={h["timer"]["enabled"]}')
    print('Host fstrim.service: ' + json.dumps(h['timer']['service'], ensure_ascii=False))
    show_findings(r)
    for vm in r['vms']:
        print(f'\n=== {vm["name"]} ({vm.get("state", "unknown")}) machine={vm.get("machine")} ===')
        show_findings(vm)
        for d in vm.get('disks', []):
            print(f'  {d["target"]}: {d["source"]} ({d["kind"]}/{d["format"]}, {d["bus"]})')
            print(f'    discard={d["discard"]}, detect_zeroes={d["detect_zeroes"]}, discard_no_unref={d["discard_no_unref"]}, cache={d["cache"]}')
            if d['allocation_bytes'] is not None:
                print(f'    Allocated: {amount(d["allocation_bytes"])}; apparent: {amount(d["apparent_bytes"])}')
            if d['mount']:
                m = d['mount']
                print(f'    Mount: {m.get("target")} ({m.get("fstype")}, {m.get("source")}); {m.get("options")}')
            for n, data in d['nodes'].items():
                b = data['discarded_bytes']
                print(f'    /dev/{n}: discard_max={data["discard_max_bytes"]}, rotational={data["rotational"]}, model={data["model"]}, cumulative discards={data["discard_operations"]}, bytes={b}; deps={",".join(data["dependencies"])}')
                if data.get('thin_pool'):
                    print('      Thin pool: ' + json.dumps(data['thin_pool']))
                if data.get('md_level'):
                    print(f'      MD level: {data["md_level"]}; raid456 discard policy={data.get("raid456_discard_safely")}')
            if d.get('qmp_current'):
                print('    QMP current (front-end then parent nodes): ' + json.dumps(d['qmp_current']))
            if d.get('shared_with'):
                print('    Shared storage endpoints: ' + json.dumps(d['shared_with'], ensure_ascii=False))
            show_findings(d, '    ')
    print('\n=== Host resources ===')
    for key in ('cpu_threads', 'running_vcpus', 'memory_bytes', 'available_memory_bytes', 'max_guest_memory_bytes', 'nominal_headroom_bytes'):
        print(f'  {key}: {h.get(key)}')
    show_findings(h)
    for key in ('guest_trim', 'host_trim'):
        if key in r:
            print(f'\n{key}: {json.dumps(r[key], ensure_ascii=False)}')
    for p in r['phases']:
        print(f'\n=== {p["phase"].upper()} phase {p.get("mount", "")} ===')
        show_findings(p)
        for n, d in p['block'].items():
            print(f'  /dev/{n}: {json.dumps(d)}')
        for d in p['disks']:
            print(f'  {d["target"]}: net_reclaimed_bytes={d["net_reclaimed_bytes"]}, QMP deltas={json.dumps(d["qmp_chain_delta"])}, SSD={d["observed_ssd"]}')
            show_findings(d, '    ')
    print('\nDevice counters are shared. QMP unmap, file allocation and SSD discard correlation is not causal proof; no NAND-level verification.')
    print('Summary: ' + ', '.join(f'{s}={r["summary"][s]}' for s in STATES) + f'; exit={r["exit_code"]}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('vms', nargs='*', help='domain names/UUIDs; default all defined domains')
    ap.add_argument('--uri', default='qemu:///system', choices=['qemu:///system', 'qemu:///session'])
    ap.add_argument('--disk', help='filter target, e.g. vda; requires exactly one VM')
    ap.add_argument('--watch', type=int, metavar='SECONDS')
    ap.add_argument('--guest-trim', action='store_true')
    ap.add_argument('--mountpoint', help='Guest mountpoint; requires --guest-trim')
    ap.add_argument('--host-trim', action='store_true', help='after observation, FITRIM entire eligible host filesystem(s)')
    ap.add_argument('--settle', type=int, default=5, help='settle seconds after active trim (default 5)')
    ap.add_argument('--timeout', type=int, default=300, help='timeout seconds for each trim command')
    ap.add_argument('--json', action='store_true', help='machine-readable output; progress on stderr')
    ap.add_argument('--no-qmp', action='store_true', help='skip QMP read-only queries in static audit')
    ap.add_argument('--strict', action='store_true', help='exit 3 on WARN/UNKNOWN if no FAIL or execution error')
    ap.add_argument('--min-host-memory-gib', type=float, help='optional nominal host memory headroom policy')
    ap.add_argument('--version', action='version', version=VERSION)
    args = ap.parse_args(argv)
    runtime = args.watch is not None or args.guest_trim
    if (runtime or args.disk) and len(args.vms) != 1:
        ap.error('runtime observation and --disk require exactly one explicit VM')
    if args.watch is not None and args.watch <= 0 or args.settle < 0 or args.timeout <= 0:
        ap.error('watch/timeout must be positive; settle must be nonnegative')
    if args.mountpoint and (not args.guest_trim or not args.mountpoint.startswith('/')):
        ap.error('--mountpoint requires --guest-trim and an absolute guest mountpoint')
    if args.host_trim and not runtime:
        ap.error('--host-trim requires --watch or --guest-trim')
    if runtime and args.no_qmp:
        ap.error('--no-qmp is for static audit only')
    if args.min_host_memory_gib is not None and not (0 <= args.min_host_memory_gib < float('inf')):
        ap.error('memory headroom policy must be finite and nonnegative')
    for tool in ['virsh', 'findmnt'] + (['fstrim'] if args.host_trim else []):
        if not shutil.which(tool):
            ap.error(f'missing required command: {tool}')
    if args.host_trim and os.geteuid() != 0:
        ap.error('--host-trim requires root')
    virsh = ['virsh', '-c', args.uri]
    r = dict(schema_version=1, version=VERSION, mode='runtime' if runtime else 'audit', findings=[], vms=[], phases=[], operation_error=False)
    timer, lvs = timer_info(), lvm_info()
    try:
        names = run(virsh + ['list', '--all', '--name']).splitlines()
        names = list(dict.fromkeys(n for n in names if n))
    except ERRORS as exc:
        names = []
        finding(r, 'FAIL', f'Unable to enumerate domains: {exc}')
        r['operation_error'] = True
    # Resolve user selectors to UUIDs to support both names and UUIDs without duplicates.
    selected = set()
    for name in args.vms:
        try:
            # Reject leading options rather than letting positional input become a virsh option.
            if name.startswith('-'):
                raise RuntimeError('invalid domain selector')
            selected.add(run(virsh + ['domuuid', name]).strip())
        except ERRORS as exc:
            finding(r, 'FAIL', f'Cannot resolve domain {name}: {exc}')
            r['operation_error'] = True
    inventory_rows = []
    for name in names:
        try:
            if name.startswith('-'):
                raise RuntimeError('unsupported domain name starting with hyphen')
            inventory_rows.append(inventory(virsh, name, timer, lvs))
        except ERRORS as exc:
            v = dict(name=name, disks=[], findings=[])
            finding(v, 'UNKNOWN', f'Unable to inspect VM: {exc}')
            inventory_rows.append(v)
    r['host'] = host_info(inventory_rows, timer, args)
    if any(not d['complete'] and not d['readonly'] for v in inventory_rows for d in v['disks']):
        finding(r['host'], 'UNKNOWN', 'Shared storage inventory may be incomplete: some writable disk paths are unresolved.')
    owners = {}
    for vm in inventory_rows:
        for d in vm['disks']:
            for n in d['leaves']:
                owners.setdefault(n, set()).add(vm['name'])
    r['vms'] = [v for v in inventory_rows if not args.vms or v.get('uuid') in selected]
    if args.vms and len(r['vms']) != len(selected):
        finding(r, 'UNKNOWN', 'Some requested domains were not available in inventory.')
        r['operation_error'] = True
    for vm in r['vms']:
        if args.disk:
            if 'state' in vm:
                vm['_full_signature'] = config_signature(vm)
            vm['disks'] = [d for d in vm['disks'] if d['target'] == args.disk]
        if not vm['disks'] and 'state' in vm:
            finding(vm, 'UNKNOWN', 'No matching disk found.')
            if args.disk or runtime:
                r['operation_error'] = True
        if vm.get('active') and not args.no_qmp:
            stats = qmp_stats(virsh, vm)
        else:
            stats = {}
        for d in vm['disks']:
            d['shared_with'] = {n: sorted(owners[n] - {vm['name']}) for n in d['leaves'] if owners[n] - {vm['name']}}
            if vm.get('active') and not args.no_qmp and not d['readonly']:
                qom_feature(virsh, vm, d)
                d['qmp_current'] = stats.get(d['target'], [])
                if not d['qmp_current'] or d['qmp_current'][0]['operations'] is None:
                    finding(d, 'UNKNOWN', 'QMP per-disk unmap counters unavailable or mapping ambiguous.')
            elif not d['readonly']:
                finding(d, 'SKIPPED', 'Runtime QMP feature/counter inspection not performed.')
    if runtime and not r['operation_error'] and len(r['vms']) == 1:
        active_test(virsh, r['vms'][0], args, r)
    for vm in r['vms']:
        vm.pop('_full_signature', None)
    findings = list(all_findings(r))
    r['summary'] = {s: sum(f['status'] == s for f in findings) for s in STATES}
    r['exit_code'] = 2 if r['operation_error'] else 1 if r['summary']['FAIL'] else 3 if args.strict and (r['summary']['WARN'] or r['summary']['UNKNOWN']) else 0
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        show_report(r)
    return r['exit_code']


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Interrupted.', file=sys.stderr)
        sys.exit(130)
    except ERRORS as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
