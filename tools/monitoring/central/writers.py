#!/usr/bin/env python3
"""Operator-only writer lifecycle; the live NGINX htpasswd is the registry.

Comments carry immutable bindings and audit history; only active records have
bcrypt entries. The managed ingress derives the host UUID from the username.
No customer authorization, tenant routing or legacy credential import.
"""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
import uuid

from inventory.inventory import load as load_hosts

NAME = re.compile(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([0-9a-f]{24})')
BCRYPT = re.compile(r'\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./A-Za-z0-9]{53}')
HEADER = '# kvm-vm managed writers v1'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def secure_directory(path):
    require(path.is_absolute() and path == path.resolve(), 'writer directory must be an absolute non-symlink path')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o750, 'writer directory must be owner-controlled mode 0750')
    return info


def secure_file(path, directory):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == directory.st_uid
            and info.st_gid == directory.st_gid and stat.S_IMODE(info.st_mode) == 0o640,
            'writer snapshot must be an owner-controlled regular file, mode 0640')


@contextmanager
def locked(directory):
    info = secure_directory(directory)
    # Lock the directory inode; the htpasswd itself is atomically replaced.
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield info, fd
    finally:
        os.close(fd)


def unique(pairs):
    result = {}
    for key, val in pairs:
        require(key not in result, 'duplicate JSON field')
        result[key] = val
    return result


def validate(records, hashes):
    active = set()
    for name, rec in records.items():
        match = NAME.fullmatch(name)
        require(match and isinstance(rec, dict) and set(rec) == {'name', 'host_id', 'history'}, 'invalid writer record')
        require(rec['name'] == name and rec['host_id'] == match[1] and str(uuid.UUID(match[1])) == match[1], 'invalid immutable binding')
        history = rec['history']
        require(isinstance(history, list) and history, 'missing writer history')
        state = None
        prev = -1
        for event in history:
            require(isinstance(event, dict) and set(event) == {'action', 'time'}, 'invalid audit event')
            action, stamp = event['action'], event['time']
            require(type(stamp) is int and stamp >= prev and stamp >= 0, 'invalid audit timestamp')
            require(action in {None: ('stage',), 'stage': ('activate', 'revoke'),
                              'activate': ('rotate', 'revoke'), 'rotate': ('rotate', 'revoke'),
                              'revoke': ()}[state], 'invalid writer state transition')
            state, prev = action, stamp
        if state in ('activate', 'rotate'):
            require(rec['host_id'] not in active, 'multiple active credentials for host')
            active.add(rec['host_id'])
            require(name in hashes and BCRYPT.fullmatch(hashes[name]), 'missing/invalid active verifier')
        else:
            require(name not in hashes, 'inactive writer has verifier')
    require(set(hashes) <= set(records), 'unregistered credential in managed snapshot')


def read(path):
    lines = path.read_text(encoding='utf-8').splitlines()
    require(lines and lines[0] == HEADER, 'not a managed writer snapshot')
    records, hashes = {}, {}
    for line in lines[1:]:
        if line.startswith('# '):
            rec = json.loads(line[2:], object_pairs_hook=unique)
            name = rec['name']
            require(name not in records, 'duplicate writer record')
            records[name] = rec
        elif line:
            name, digest = line.split(':', 1)
            require(name not in hashes, 'duplicate verifier')
            hashes[name] = digest
    validate(records, hashes)
    return records, hashes


def save(path, records, hashes, info, directory_fd):
    validate(records, hashes)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.writers-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            os.fchown(out.fileno(), info.st_uid, info.st_gid)
            os.fchmod(out.fileno(), 0o640)
            print(HEADER, file=out)
            for name, rec in sorted(records.items()):
                print('# ' + json.dumps(rec, sort_keys=True), file=out)
                if name in hashes:
                    print(name + ':' + hashes[name], file=out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        os.fsync(directory_fd)
    finally:
        Path(tmp).unlink(missing_ok=True)


def password_verifier(name):
    # No password in argv/env, logs or response. bcrypt truncates beyond 72 bytes.
    password = sys.stdin.buffer.readline(75).removesuffix(b'\n').removesuffix(b'\r')
    require(16 <= len(password) <= 72 and not any(c < 33 or c > 126 for c in password)
            and not sys.stdin.buffer.read(1), 'provide one 16–72 byte printable ASCII password on stdin')
    result = subprocess.run(['htpasswd', '-niB', '-C', '10', name], input=password + b'\n',
                            capture_output=True, check=True, timeout=20)
    user, digest = result.stdout.decode().strip().split(':', 1)
    require(user == name and BCRYPT.fullmatch(digest), 'invalid bcrypt output')
    return digest


def change(records, hashes, action, name, known_hosts, verifier=password_verifier, now=None):
    rec = records[name]
    last = rec['history'][-1]
    now = int(time.time()) if now is None else now
    require(now >= last['time'], 'system clock moved backwards; correct time before changing credentials')
    state = last['action']
    if action == 'revoke' and state == 'revoke':
        return
    if action == 'activate':
        require(state == 'stage' and rec['host_id'] in known_hosts, 'activation requires a staged writer and known host')
        require(not any(r['host_id'] == rec['host_id'] and n in hashes for n, r in records.items()), 'revoke the previous writer before activating a new name')
    elif action == 'rotate':
        require(name in hashes and rec['host_id'] in known_hosts, 'rotation requires an active writer and known host')
    else:
        require(action == 'revoke' and state != 'revoke', 'invalid writer transition')
    if action == 'revoke':
        hashes.pop(name, None)
    else:
        hashes[name] = verifier(name)
    rec['history'].append({'action': action, 'time': now})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--hosts', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init').add_argument('group', help='NGINX worker group (e.g. www-data)')
    sub.add_parser('stage').add_argument('host_id')
    for action in ('activate', 'rotate', 'revoke'):
        sub.add_parser(action).add_argument('name')
    for action in ('list', 'check'):
        sub.add_parser(action)
    args = parser.parse_args()
    directory = args.directory.absolute()
    path = directory / 'htpasswd'
    try:
        require(directory == directory.resolve(), 'refusing symlink writer directory')
        if args.action == 'init':
            gid = grp.getgrnam(args.group).gr_gid
            if not directory.exists():
                directory.mkdir(mode=0o750)  # parent is operator-provisioned
                os.chown(directory, os.geteuid(), gid)
                os.chmod(directory, 0o750)
            require(secure_directory(directory).st_gid == gid, 'writer directory group mismatch')
        with locked(directory) as (info, fd):
            if args.action == 'init' and not os.path.lexists(path):
                save(path, {}, {}, info, fd)
            secure_file(path, info)
            records, hashes = read(path)
            if args.action == 'init':
                print('Managed writer snapshot initialized; configure the dedicated NGINX ingress.')
                return
            if args.action == 'list':
                print(json.dumps(list(records.values()), indent=2))
                return
            # Revocation remains possible even if inventory was deleted/corrupted.
            known = set() if args.action == 'revoke' else {r['host_id'] for r in load_hosts(args.hosts)}
            if args.action == 'check':
                require(all(records[n]['host_id'] in known for n in hashes), 'active writer references unknown inventory host')
                print('OK: snapshot and inventory consistent (live ingress not verified)')
                return
            if args.action == 'stage':
                require(args.host_id in known, 'unknown inventory host UUID')
                name = args.host_id + '.' + secrets.token_hex(12)
                require(name not in records, 'credential ID collision')
                records[name] = {'name': name, 'host_id': args.host_id,
                                 'history': [{'action': 'stage', 'time': int(time.time())}]}
            else:
                name = args.name
                change(records, hashes, args.action, name, known)
            save(path, records, hashes, info, fd)
            print(name)
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError):
        # Never echo untrusted malformed JSON, verifier output or password data.
        parser.exit(1, 'Writer operation failed; check permissions, inventory, state transition and password input. Snapshot not intentionally reset.\n')


if __name__ == '__main__':
    main()
