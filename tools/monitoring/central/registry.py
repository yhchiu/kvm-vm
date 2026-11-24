#!/usr/bin/env python3
"""Central operator-only tenant/VM registry. Metadata, not authorization."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
import uuid

# Kept separate from the deployed legacy inventory exporter. No migration or
# extra fields are required in hosts.json, and no new public endpoint exists.
TENANT = re.compile(r'^[a-z][a-z0-9-]{0,62}$')
INTERNAL = 'internal'
VERSION = 1


def check(condition, message):
    if not condition:
        raise ValueError(message)


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def timestamp(value):
    return type(value) is int and value >= 0


def name_ok(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128 and
            value == value.strip() and all(c.isprintable() for c in value))


def empty_registry():
    return {'version': VERSION, 'tenants': {}, 'vms': {}}


def validate(data):
    check(isinstance(data, dict) and set(data) == {'version', 'tenants', 'vms'}
          and type(data['version']) is int and data['version'] == VERSION,
          'unsupported or invalid registry schema')
    tenants, vms = data['tenants'], data['vms']
    check(isinstance(tenants, dict) and isinstance(vms, dict), 'invalid registry maps')
    for tenant, rec in tenants.items():
        check(isinstance(tenant, str) and TENANT.fullmatch(tenant) and tenant != INTERNAL,
              'invalid or reserved tenant ID')
        check(isinstance(rec, dict) and set(rec) == {'name', 'enabled'} and
              name_ok(rec['name']) and type(rec['enabled']) is bool, 'invalid tenant record')
    for vm_id, rec in vms.items():
        check(canonical_uuid(vm_id), 'invalid VM UUID')
        check(isinstance(rec, dict) and set(rec) == {'name', 'retired', 'history'} and
              name_ok(rec['name']) and type(rec['retired']) is bool, 'invalid VM record')
        history = rec['history']
        check(isinstance(history, list) and history, 'missing VM history')
        last_end = None
        for i, entry in enumerate(history):
            check(isinstance(entry, dict) and set(entry) == {'host_id', 'tenant_id', 'start', 'end'},
                  'invalid ownership interval')
            check(canonical_uuid(entry['host_id']), 'invalid history host UUID')
            tenant = entry['tenant_id']
            check(isinstance(tenant, str) and (tenant == INTERNAL or tenant in tenants), 'unknown history tenant')
            start, end = entry['start'], entry['end']
            check(timestamp(start) and (end is None or (timestamp(end) and end > start)), 'invalid interval timestamps')
            if i:
                check(last_end is not None and last_end == start, 'history must be contiguous and ordered')
            if i < len(history) - 1:
                check(end is not None, 'only final interval can be open')
            last_end = end
        check((last_end is not None) == rec['retired'], 'retirement and history disagree')
        if not rec['retired']:
            tenant = history[-1]['tenant_id']
            check(tenant == INTERNAL or tenants[tenant]['enabled'], 'active VM assigned to disabled tenant')
    return data


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        check(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def private_directory(path):
    check(not path.is_symlink(), 'refusing symlink registry directory')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    check(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077 and info.st_uid == os.geteuid(),
          'registry directory must be owned by the operator and private (0700)')


def load(path):
    if not path.exists() and not path.is_symlink():
        return empty_registry()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        check(stat.S_ISREG(info.st_mode) and not info.st_mode & 0o077 and info.st_uid == os.geteuid(),
              'registry must be an operator-owned private regular file')
        return validate(json.load(stream, object_pairs_hook=unique_object))


@contextmanager
def writer_lock(path):
    private_directory(path.parent)
    fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077,
              'invalid registry lock file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def save(path, data):
    validate(data)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.registry-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def known_host(path, host_id):
    check(canonical_uuid(host_id), 'invalid Host UUID')
    # Reuse the existing inventory validator, without modifying its data.
    from inventory.inventory import load as load_hosts
    check(any(r['host_id'] == host_id for r in load_hosts(path)), 'unknown Host UUID; register it in inventory first')


def require_tenant(data, tenant):
    check(tenant == INTERNAL or (tenant in data['tenants'] and data['tenants'][tenant]['enabled']),
          'unknown or disabled tenant')


def active_vm(data, vm_id):
    rec = data['vms'].get(vm_id)
    check(rec is not None and not rec['retired'], 'unknown or retired VM UUID')
    return rec


def interval_at(rec, at):
    return next((r for r in rec['history'] if r['start'] <= at and
                 (r['end'] is None or at < r['end'])), None)


def change(data, hosts, args):
    command = args.command
    if command == 'tenant-add':
        check(TENANT.fullmatch(args.tenant_id) and args.tenant_id != INTERNAL, 'invalid or reserved tenant ID')
        check(args.tenant_id not in data['tenants'], 'tenant already exists')
        check(name_ok(args.name), 'invalid display name')
        data['tenants'][args.tenant_id] = {'name': args.name, 'enabled': True}
    elif command in ('tenant-enable', 'tenant-disable'):
        tenant = args.tenant_id
        check(tenant in data['tenants'], 'unknown or reserved tenant')
        if command == 'tenant-disable':
            check(not any(not r['retired'] and r['history'][-1]['tenant_id'] == tenant
                          for r in data['vms'].values()), 'unassign active VMs before disabling tenant')
        data['tenants'][tenant]['enabled'] = command == 'tenant-enable'
    elif command == 'vm-add':
        check(canonical_uuid(args.vm_id), 'invalid VM UUID')
        check(args.vm_id not in data['vms'], 'VM UUID already registered (including retired VMs)')
        check(name_ok(args.name), 'invalid display name')
        known_host(hosts, args.host_id)
        require_tenant(data, args.tenant)
        data['vms'][args.vm_id] = {'name': args.name, 'retired': False, 'history': [
            {'host_id': args.host_id, 'tenant_id': args.tenant, 'start': args.at, 'end': None}]}
    elif command == 'vm-rename':
        rec = active_vm(data, args.vm_id)
        check(name_ok(args.name), 'invalid display name')
        rec['name'] = args.name  # display metadata only; no ownership transition
    else:
        rec = active_vm(data, args.vm_id)
        current = rec['history'][-1]
        if command == 'vm-retire':
            check(args.at > current['start'], 'transition must be later than current interval start')
            current['end'] = args.at
            rec['retired'] = True
            return
        host, tenant = current['host_id'], current['tenant_id']
        if command == 'vm-move':
            known_host(hosts, args.host_id)
            host = args.host_id
        elif command == 'vm-assign':
            require_tenant(data, args.tenant_id)
            tenant = args.tenant_id
        elif command == 'vm-unassign':
            tenant = INTERNAL
        if (host, tenant) == (current['host_id'], current['tenant_id']):
            return  # idempotent no-op
        check(args.at > current['start'], 'transition must be later than current interval start')
        current['end'] = args.at
        rec['history'].append({'host_id': host, 'tenant_id': tenant, 'start': args.at, 'end': None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, required=True)
    parser.add_argument('--hosts', type=Path, required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    a = sub.add_parser('tenant-add'); a.add_argument('tenant_id'); a.add_argument('name')
    for cmd in ('tenant-enable', 'tenant-disable'):
        sub.add_parser(cmd).add_argument('tenant_id')
    a = sub.add_parser('vm-add'); a.add_argument('vm_id'); a.add_argument('host_id'); a.add_argument('name')
    a.add_argument('--tenant', default=INTERNAL); a.add_argument('--at', type=int)
    a = sub.add_parser('vm-rename'); a.add_argument('vm_id'); a.add_argument('name')
    for cmd, extra in (('vm-move', 'host_id'), ('vm-assign', 'tenant_id'), ('vm-unassign', None), ('vm-retire', None)):
        a = sub.add_parser(cmd); a.add_argument('vm_id'); a.add_argument('--at', type=int)
        if extra:
            a.add_argument(extra)
    a = sub.add_parser('vm-at'); a.add_argument('vm_id'); a.add_argument('--at', type=int, required=True)
    args = parser.parse_args()
    try:
        private_directory(args.file.parent)
        if args.command == 'list':
            print(json.dumps(load(args.file), indent=2, sort_keys=True))
        elif args.command == 'vm-at':
            check(timestamp(args.at), 'invalid timestamp')
            data = load(args.file)
            check(args.vm_id in data['vms'], 'unknown VM UUID')
            print(json.dumps(interval_at(data['vms'][args.vm_id], args.at), sort_keys=True))
        else:
            with writer_lock(args.file):
                if hasattr(args, 'at'):
                    now = int(time.time())
                    args.at = now if args.at is None else args.at
                    check(timestamp(args.at) and args.at <= now, 'invalid or future effective timestamp')
                data = load(args.file)
                change(data, args.hosts, args)
                save(args.file, data)
            print(f'{args.command}: OK (metadata only)')
    except (OSError, ValueError, TypeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
