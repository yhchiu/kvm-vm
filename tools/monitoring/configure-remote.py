#!/usr/bin/env python3
"""Render remote-write configs using Python stdlib; secrets never enter YAML/env.

JSON output is valid YAML for both Prometheus and vmagent.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import uuid
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PROMETHEUS_SECRET = '/etc/prometheus/remote-password'
VMAGENT_SECRET = '/etc/vmagent/remote-password'
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def fail(reason):
    raise ValueError(reason)


def atomic_save(path: Path, data: str, mode: int):
    # Parent must already be private, and no symlink may be followed.
    if path.is_symlink():
        fail(f'refusing symlink: {path}')
    temp = path.with_name('.' + path.name + '.new')
    if temp.is_symlink():
        fail(f'refusing symlink: {temp}')
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, mode)
    finally:
        if temp.exists():
            temp.unlink()


def validate_endpoint(value):
    if any(c.isspace() or ord(c) < 32 for c in value):
        fail('Remote write URL cannot contain whitespace or control characters')
    target = urlsplit(value)
    if not re.fullmatch(r'https://(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\])(?::[0-9]{1,5})?/api/v1/write/?', value):
        fail('Remote write URL must be a valid HTTPS endpoint ending with /api/v1/write')
    if (target.scheme != 'https' or not target.hostname or target.username or
            target.password or target.query or target.fragment or
            target.path.rstrip('/') != '/api/v1/write'):
        fail('Remote write URL must be HTTPS and end in /api/v1/write, without embedded credentials, query or fragment')
    return value


def label(name, value):
    if not IDENTIFIER.fullmatch(value):
        fail(f'{name} must be 1-128 characters: letters, digits, dot, underscore or hyphen')
    return value


def remote_config(args):
    cfg = args.directory
    if cfg.is_symlink():
        fail('configuration directory may not be a symlink')
    cfg.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cfg, 0o700)
    path = cfg / 'remote.json'
    if path.is_symlink():
        fail('Refusing symlink remote.json')
    if path.is_file():
        state = json.loads(path.read_text())
        if args.url and args.url != state['url']:
            fail('Existing remote endpoint differs. Reconfigure via a reviewed edit to remote.json, then render again')
        if args.user and args.user != state['user']:
            fail('Existing remote username differs; explicitly edit remote.json and render again')
        if args.host_id and args.host_id != state['host_id']:
            fail('Host ID is immutable; do not replace it during updates or mode switches')
        if args.host and args.host != state['host']:
            fail('Existing Host label differs; explicitly edit remote.json and render again')
        if args.site and args.site != state['site']:
            fail('Existing Site label differs; explicitly edit remote.json and render again')
    else:
        if not args.url:
            fail('First deployment requires KVM_VM_REMOTE_WRITE_URL=https://.../api/v1/write')
        state = {
            'url': args.url,
            'host_id': args.host_id or str(uuid.uuid4()),
            'host': args.host or socket.gethostname().split('.')[0].lower(),
            'site': args.site or 'default',
            'user': args.user or '',
        }
        state['user'] = state['user'] or state['host_id']
    validate_endpoint(state['url'])
    for key in ('host_id', 'host', 'site', 'user'):
        label(key, state[key])
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', state['host_id']):
        fail('host_id must be a lowercase canonical UUID')
    secret = cfg / 'remote-password'
    if secret.is_symlink():
        fail('Refusing symlink remote-password')
    if not secret.exists():
        if not args.password_file:
            fail('First deployment requires KVM_VM_REMOTE_PASSWORD_FILE pointing to a readable password file')
        source = Path(args.password_file)
        if not source.is_file() or source.is_symlink():
            fail('Password source must be a regular file, not a symlink')
        password = source.read_text().rstrip('\r\n')
        if not password or any(c in password for c in '\r\n\x00'):
            fail('Password file must contain one nonempty line')
        # The host parent is mode 0700. Mode 0644 is needed for non-root
        # Prometheus/vmagent processes inside containers to read the bind mount.
        atomic_save(secret, password + '\n', 0o644)
    elif args.password_file:
        source = Path(args.password_file)
        if source.is_symlink() or not source.is_file() or source.read_text().rstrip('\r\n') != secret.read_text().rstrip('\r\n'):
            fail('Existing credential differs; explicitly rotate remote-password before reusing this host')
    if not path.exists():
        atomic_save(path, json.dumps(state, indent=2) + '\n', 0o600)
    return state


def scrape_config(state, agent=False):
    labels = {k: state[k] for k in ('host_id', 'host', 'site')}
    jobs = [dict(job_name='kvm-vm', static_configs=[dict(targets=['127.0.0.1:9179'], labels=labels)])]
    jobs.append(dict(job_name='node-exporter', static_configs=[dict(targets=['127.0.0.1:9100'], labels=labels)]))
    if agent:
        jobs.append(dict(job_name='vmagent', static_configs=[dict(targets=['127.0.0.1:8429'], labels=labels)]))
    return jobs


def setting(path, name, default):
    if path.is_symlink():
        fail(f'Refusing symlink {path.name}')
    if not path.exists():
        return default
    values = [line.split('=', 1)[1] for line in path.read_text().splitlines()
              if line.startswith(name + '=')]
    if len(values) > 1:
        fail(f'Duplicate {name} setting')
    return values[0] if values else default


def render(args):
    cfg = args.directory
    if cfg.is_symlink():
        fail('configuration directory may not be a symlink')
    saved_limit = setting(cfg / 'remote.env', 'REMOTE_DISK_QUEUE_LIMIT', '2147483648')
    limit = args.disk_queue_limit or saved_limit
    if not re.fullmatch(r'[1-9][0-9]{0,12}', limit) or not 512 * 1024**2 <= int(limit) <= 1024**4:
        fail('Agent disk queue limit must be bytes between 512 MiB and 1 TiB')
    if (cfg / 'remote.env').exists() and limit != saved_limit and not args.reconfigure:
        fail('Queue limit change requires explicit reconfigure')
    port = setting(cfg / '.env', 'PROMETHEUS_PORT', os.getenv('PROMETHEUS_PORT', '9090'))
    if not re.fullmatch(r'[1-9][0-9]{0,4}', port) or int(port) > 65535:
        fail('Invalid local Prometheus port')
    state = remote_config(args)
    labels = {k: state[k] for k in ('host_id', 'host', 'site')}
    signature_file = cfg / '.remote-rendered-state'
    signature = json.dumps(state, sort_keys=True) + '\n'
    if signature_file.exists() and signature_file.read_text() != signature and not args.reconfigure:
        fail('Remote identity/endpoint changed: rerun with KVM_VM_REMOTE_RECONFIGURE=1 after review')
    agent = {'global': {'scrape_interval': '15s', 'scrape_timeout': '10s'},
             'scrape_configs': scrape_config(state, True)}
    hybrid = {'global': {'scrape_interval': '15s', 'scrape_timeout': '10s',
                         'external_labels': labels},
              'scrape_configs': [dict(job_name='kvm-vm', static_configs=[dict(targets=['127.0.0.1:9179'])]),
                                 dict(job_name='node-exporter', static_configs=[dict(targets=['127.0.0.1:9100'])]),
                                 dict(job_name='prometheus-writer', static_configs=[dict(targets=['127.0.0.1:'+port])],
                                      metric_relabel_configs=[dict(source_labels=['__name__'],
                                          regex='prometheus_remote_storage_.*|process_start_time_seconds', action='keep')])],
              'remote_write': [dict(url=state['url'], basic_auth={
                  'username': state['user'], 'password_file': PROMETHEUS_SECRET},
                  queue_config={'retry_on_http_429': True})]}
    # External labels are added only by remote write, while local Prometheus
    # keeps its normal VM label schema. The remote series remain host-unique.
    if args.reconfigure or not (cfg / 'vmagent-scrape.yml').exists():
        atomic_save(cfg / 'vmagent-scrape.yml', json.dumps(agent, indent=2) + '\n', 0o644)
    if args.reconfigure or not (cfg / 'prometheus-hybrid.yml').exists():
        atomic_save(cfg / 'prometheus-hybrid.yml', json.dumps(hybrid, indent=2) + '\n', 0o644)
    # Compose interpolates these non-secret values from a private .env.
    env = '\n'.join([
        'REMOTE_WRITE_URL=' + state['url'],
        'REMOTE_WRITE_USERNAME=' + state['user'],
        'REMOTE_DISK_QUEUE_LIMIT=' + limit,
        '',
    ])
    if args.reconfigure or not (cfg / 'remote.env').exists():
        atomic_save(cfg / 'remote.env', env, 0o600)
    for source, dest in [('remote/compose.agent.yaml', 'compose.agent.yaml'),
                         ('remote/compose.hybrid.yaml', 'compose.hybrid.yaml')]:
        if args.reconfigure or not (cfg / dest).exists():
            shutil.copyfile(ROOT / source, cfg / dest)
            os.chmod(cfg / dest, 0o644)
    if args.reconfigure or not signature_file.exists():
        atomic_save(signature_file, signature, 0o600)
    print(f"Remote write configured for host={state['host']} host_id={state['host_id']} site={state['site']}")
    print('Credentials are not written into Compose, metrics or command-line arguments.')


def replace_password(cfg):
    # Called by the locked Host lifecycle command after confirming both writers
    # are absent. Only the password changes, so destination and queue IDs stay.
    if cfg.is_symlink() or not cfg.is_dir() or cfg.stat().st_mode & 0o077:
        fail('Require existing private monitoring directory')
    if any(os.path.lexists(cfg / name) for name in ('.mode', '.mode.pending')):
        fail('Stop the current monitoring deployment first')
    for name in ('remote.json', 'remote-password'):
        if (cfg / name).is_symlink() or not (cfg / name).is_file():
            fail('Require existing regular remote identity and password files')
    state = json.loads((cfg / 'remote.json').read_text())
    validate_endpoint(state['url'])
    password = sys.stdin.buffer.readline(75).removesuffix(b'\n').removesuffix(b'\r')
    if not 16 <= len(password) <= 72 or any(c < 33 or c > 126 for c in password) or sys.stdin.buffer.read(1):
        fail('Provide one 16–72 byte printable ASCII password on stdin')
    atomic_save(cfg / 'remote-password', password.decode('ascii') + '\n', 0o644)
    print('Password replaced; restart the same mode and verify delivery. Identity and queue unchanged.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--replace-password', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--reconfigure', action='store_true',
                        default=os.getenv('KVM_VM_REMOTE_RECONFIGURE') == '1')
    parser.add_argument('--url', default=os.getenv('KVM_VM_REMOTE_WRITE_URL'))
    parser.add_argument('--user', default=os.getenv('KVM_VM_REMOTE_USERNAME'))
    parser.add_argument('--password-file', default=os.getenv('KVM_VM_REMOTE_PASSWORD_FILE'))
    parser.add_argument('--host-id', default=os.getenv('KVM_VM_MONITOR_HOST_ID'))
    parser.add_argument('--host', default=os.getenv('KVM_VM_MONITOR_HOST'))
    parser.add_argument('--site', default=os.getenv('KVM_VM_MONITOR_SITE'))
    parser.add_argument('--disk-queue-limit', default=os.getenv('KVM_VM_REMOTE_DISK_QUEUE_LIMIT'))
    args = parser.parse_args()
    try:
        if args.replace_password:
            replace_password(args.directory)
        else:
            render(args)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
