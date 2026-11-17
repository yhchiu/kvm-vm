#!/usr/bin/env python3
"""File-backed Host inventory and Prometheus exposition (stdlib only).

The inventory is CENTRAL-only. Registration is deliberately independent of
Remote Write credentials: authorization to ingest does not mean a host must
be expected online.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
TENANT = re.compile(r'^[a-z][a-z0-9-]{0,62}$')
DEFAULT_TENANT = 'internal'  # Legacy records remain internal/operator-only.


def validate_record(record):
    required = {'host_id', 'host', 'site', 'enabled'}
    if not isinstance(record, dict) or not required.issubset(record) or not set(record).issubset(required | {'tenant_id'}):
        raise ValueError('invalid inventory record fields')
    try:
        uid = uuid.UUID(record['host_id'])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError('invalid host_id UUID') from exc
    if str(uid) != record['host_id']:
        raise ValueError('host_id must be canonical lowercase UUID')
    for key in ('host', 'site'):
        if not isinstance(record[key], str) or not NAME.fullmatch(record[key]):
            raise ValueError(f'invalid {key} label')
    tenant_id = record.get('tenant_id', DEFAULT_TENANT)
    if not isinstance(tenant_id, str) or not TENANT.fullmatch(tenant_id):
        raise ValueError('invalid tenant_id (lowercase slug required)')
    if type(record['enabled']) is not bool:
        raise ValueError('enabled must be a boolean')
    return record


def load(path):
    if path.is_symlink():
        raise ValueError('refusing symlink inventory file')
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(records, list):
        raise ValueError('inventory must be a list')
    seen = set()
    for record in records:
        validate_record(record)
        if record['host_id'] in seen:
            raise ValueError('duplicate host_id')
        seen.add(record['host_id'])
    return records


def save(path, records):
    # Strict validation before any writes, and atomic rename for live readers.
    seen = set()
    for record in records:
        validate_record(record)
        if record['host_id'] in seen:
            raise ValueError('duplicate host_id')
        seen.add(record['host_id'])
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('refusing symlink inventory location')
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if not path.exists():
        # Refuse to replace a non-regular existing object.
        pass
    elif not path.is_file():
        raise ValueError('inventory path must be a regular file')
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.inventory-', suffix='.json')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(records, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def metrics(path):
    expected = [
        '# HELP kvm_vm_host_expected Whether an enrolled KVM host is expected to report metrics.',
        '# TYPE kvm_vm_host_expected gauge',
    ]
    owners = [
        '# HELP kvm_vm_host_tenant_info Operator-assigned host-to-tenant inventory metadata; NOT an access-control mechanism.',
        '# TYPE kvm_vm_host_tenant_info gauge',
    ]
    for rec in load(path):
        # Trusted local operator inventory, not the remote-write payload.
        # Do NOT use this series for authorizing customer data queries.
        tenant = rec.get('tenant_id', DEFAULT_TENANT)
        owners.append(f'kvm_vm_host_tenant_info{{host_id="{rec["host_id"]}",tenant_id="{tenant}"}} 1')
        if rec['enabled']:
            # Preserve existing expected-host labels and alerts for old records.
            fields = ','.join(f'{key}="{rec[key]}"' for key in ('host_id', 'host', 'site'))
            expected.append(f'kvm_vm_host_expected{{{fields}}} 1')
    return ('\n'.join(expected + owners) + '\n').encode('utf-8')


def serve(path, bind, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/metrics', '/healthz'):
                self.send_error(404)
                return
            try:
                body = metrics(path) if self.path == '/metrics' else b'ok\n'
            except (OSError, ValueError, json.JSONDecodeError):
                self.send_error(503, 'Inventory unavailable')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8' if self.path == '/metrics' else 'text/plain')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    ThreadingHTTPServer((bind, port), Handler).serve_forever()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--file', type=Path, required=True)
    sub = ap.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add')
    add.add_argument('host_id')
    add.add_argument('host')
    add.add_argument('site')
    add.add_argument('--tenant', dest='tenant_id', default=None,
                     help='operator-assigned tenant slug (default: internal)')
    owner = sub.add_parser('set-tenant', help='assign tenant to an existing host (not authorization)')
    owner.add_argument('host_id')
    owner.add_argument('tenant_id')
    for cmd in ('enable', 'disable', 'remove'):
        sub.add_parser(cmd).add_argument('host_id')
    sub.add_parser('list')
    sub.add_parser('metrics')
    web = sub.add_parser('serve')
    web.add_argument('--bind', default='0.0.0.0')
    web.add_argument('--port', type=int, default=9187)
    args = ap.parse_args()
    try:
        if args.command == 'serve':
            serve(args.file, args.bind, args.port)
            return
        records = load(args.file)
        if args.command == 'list':
            print(json.dumps(records, indent=2))
        elif args.command == 'metrics':
            sys.stdout.buffer.write(metrics(args.file))
        elif args.command == 'add':
            new = dict(host_id=args.host_id, host=args.host, site=args.site, enabled=True)
            if args.tenant_id is not None:
                new['tenant_id'] = args.tenant_id
            validate_record(new)
            if any(x['host_id'] == args.host_id for x in records):
                raise ValueError('host_id already exists; use disable/enable or remove explicitly')
            records.append(new)
            save(args.file, records)
            print('Registered host (expected online):', args.host_id)
        else:
            target = next((r for r in records if r['host_id'] == args.host_id), None)
            if target is None:
                raise ValueError('unknown host_id')
            if args.command == 'remove':
                records.remove(target)
            elif args.command == 'set-tenant':
                if not TENANT.fullmatch(args.tenant_id):
                    raise ValueError('invalid tenant_id (lowercase slug required)')
                target['tenant_id'] = args.tenant_id
            else:
                target['enabled'] = args.command == 'enable'
            save(args.file, records)
            print(f'{args.command}: {args.host_id}')
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
