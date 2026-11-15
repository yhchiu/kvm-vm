#!/usr/bin/env python3
"""Add a trusted private lab CA to the *disposable* Agent and Hybrid configs.

Only for the isolated E2E lab. Production TLS provisioning is a separate task.
"""
from pathlib import Path
import json
import sys


def inject(config_dir: Path):
    agent = config_dir / 'compose.agent.yaml'
    a = agent.read_text()
    changes = [
        ('      - -remoteWrite.tmpDataPath=/vmagent-queue',
         '      - -remoteWrite.tlsCAFile=/etc/vmagent/lab-ca.pem\n', '-remoteWrite.tlsCAFile='),
        ('      - ./remote-password:/etc/vmagent/remote-password:ro',
         '\n      - ./lab-ca.pem:/etc/vmagent/lab-ca.pem:ro',
         './lab-ca.pem:/etc/vmagent/lab-ca.pem:ro'),
    ]
    for anchor, insert, marker in changes:
        if marker not in a:
            if a.count(anchor) != 1:
                raise ValueError(f'vmagent template missing/ambiguous anchor: {anchor}')
            a = a.replace(anchor, insert + anchor if marker.startswith('-remoteWrite') else anchor + insert)
    agent.write_text(a)

    hybrid = config_dir / 'compose.hybrid.yaml'
    h = hybrid.read_text()
    anchor = '      - ./remote-password:/etc/prometheus/remote-password:ro'
    mount = '      - ./lab-ca.pem:/etc/prometheus/lab-ca.pem:ro'
    if mount not in h:
        if h.count(anchor) != 1:
            raise ValueError('hybrid template missing/ambiguous password volume')
        h = h.replace(anchor, anchor + '\n' + mount)
    hybrid.write_text(h)

    prometheus = config_dir / 'prometheus-hybrid.yml'
    config = json.loads(prometheus.read_text())
    remote = config['remote_write']
    if len(remote) != 1:
        raise ValueError('unexpected hybrid remote_write configuration')
    remote[0]['tls_config'] = {'ca_file': '/etc/prometheus/lab-ca.pem'}
    prometheus.write_text(json.dumps(config, indent=2) + '\n')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: inject_lab_ca.py CONFIG_DIR')
    inject(Path(sys.argv[1]))
