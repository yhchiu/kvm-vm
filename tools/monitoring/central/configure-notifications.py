#!/usr/bin/env python3
"""Explicit, secret-file backed Alertmanager notification configuration.

No secret is interpolated into a compose environment variable or command line.
Operator must run central 'up' afterwards to reload the static config.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import urlsplit

EMAIL = re.compile(r'^[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')
SMTP = re.compile(r'^([A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):([0-9]{1,5})$')


def atomic_write(path, content, mode):
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('refusing symlink configuration path')
    fd, tmp = tempfile.mkstemp(prefix='.notification-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_secret(source):
    path = Path(source)
    if path.is_symlink() or not path.is_file():
        raise ValueError('secret source must be a regular file, not a symlink')
    value = path.read_text(encoding='utf-8').rstrip('\r\n')
    if not value or any(c in value for c in '\x00\r\n'):
        raise ValueError('secret source must contain one nonempty line')
    return value


def configure(cfg, mode, extra):
    if cfg.is_symlink() or not (cfg / '.env').is_file():
        raise ValueError('central stack not initialized, or config path is a symlink')
    destination = cfg / 'alertmanager' / 'alertmanager.yml'
    if destination.is_symlink() or not destination.is_file():
        raise ValueError('invalid Alertmanager config path')
    secrets_dir = cfg / 'alertmanager' / 'secrets'
    if secrets_dir.is_symlink():
        raise ValueError('refusing symlink secrets directory')
    secrets_dir.mkdir(mode=0o755, exist_ok=True)
    os.chmod(secrets_dir, 0o755)  # Container uses a non-root UID.
    config = {
        'global': {'resolve_timeout': '5m'},
        'route': {'receiver': mode, 'group_by': ['alertname','host_id'],
                  'group_wait': '30s', 'group_interval': '5m', 'repeat_interval': '4h'},
        'receivers': [{'name': mode}],
    }
    receiver = config['receivers'][0]
    if mode == 'webhook':
        value = read_secret(extra[0])
        url = urlsplit(value)
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment or any(c.isspace() for c in value):
            raise ValueError('webhook must be a well-formed HTTPS URL without embedded credentials or fragment')
        # Token in query string is supported, but never written into config.
        secret_file = secrets_dir / 'webhook-url'
        atomic_write(secret_file, value + '\n', 0o644)
        receiver['webhook_configs'] = [{'url_file': '/etc/alertmanager/secrets/webhook-url', 'send_resolved': True}]
    elif mode == 'email':
        address, smarthost, sender, user, secret_source = extra
        if not EMAIL.fullmatch(address) or not EMAIL.fullmatch(sender) or not re.fullmatch(r'[A-Za-z0-9._@-]{1,128}', user):
            raise ValueError('invalid email address')
        match = SMTP.fullmatch(smarthost)
        if not match or not (1 <= int(match.group(2)) <= 65535):
            raise ValueError('SMTP server must be HOST:PORT')
        password = read_secret(secret_source)
        atomic_write(secrets_dir / 'smtp-password', password + '\n', 0o644)
        receiver['email_configs'] = [{
            'to': address, 'from': sender, 'smarthost': smarthost,
            'auth_username': user, 'auth_password_file': '/etc/alertmanager/secrets/smtp-password',
            'require_tls': True, 'force_implicit_tls': int(match.group(2)) == 465,
            'send_resolved': True,
        }]
    else:
        raise ValueError('unsupported notification mode')
    atomic_write(destination, json.dumps(config, indent=2) + '\n', 0o644)
    print(f'Configured Alertmanager {mode} receiver. Run central reload to apply; verify test delivery.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('mode', choices=('webhook', 'email'))
    parser.add_argument('values', nargs='*')
    args = parser.parse_args()
    count = 1 if args.mode == 'webhook' else 5
    if len(args.values) != count:
        parser.error('webhook expects URL_FILE; email expects TO SMTP_HOST:PORT FROM SMTP_USERNAME PASSWORD_FILE')
    try:
        configure(args.directory, args.mode, args.values)
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
