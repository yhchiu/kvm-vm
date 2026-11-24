#!/usr/bin/env python3
"""Verify managed ingress on the disposable dual-NAT lab and pinned Single.

Only synthetic series, unique auth directory/site and a separate TLS port are
used. No existing writer, credential or customer data is changed.
"""
import argparse
import json
import secrets
import shlex
import struct
import time

import lab


def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    out.append(n)
    return bytes(out)


def field(number, data):
    return varint(number * 8 + 2) + varint(len(data)) + data


def remote_write(labels, ts):
    # Prometheus Remote Write 1.0 protobuf, one sample, Snappy block literal.
    series = b''.join(field(1, field(1,k.encode())+field(2,v.encode())) for k,v in sorted(labels.items()))
    series += field(2, b'\x09'+struct.pack('<d',1.0)+b'\x10'+varint(ts))
    data = field(1,series)
    size = len(data)-1
    if size < 60:
        tag = bytes([size << 2])
    else:
        width = (size.bit_length()+7)//8
        tag = bytes([(59+width)<<2])+size.to_bytes(width,'little')
    return varint(len(data))+tag+data


def exercise(t):
    token = secrets.token_hex(8)
    directory = '/etc/nginx/kvm-lab/writers-'+token
    site = '/etc/nginx/sites-enabled/writers-'+token+'.conf'
    client = '/root/kvm-writer-'+token
    central_secret = '/root/kvm-writer-'+token+'.pwd'
    setup = '/opt/kvm-vm/tools/setup-central-monitoring.sh'
    cmd = f'sudo env KVM_VM_WRITER_DIR={directory} {setup} writers'
    host_id = t.data['credentials']['host-a']['host_id']
    forged = t.data['credentials']['host-b']['host_id']
    name = None
    put = lambda node, path, content: t.scp_bytes(node, content, path, '0600')
    call = lambda command: t.exec_guest('central', command)
    def change(action,password):
        put('central',central_secret,(password+'\n').encode())
        try:
            call(f'sudo sh -ec {shlex.quote(f"KVM_VM_WRITER_DIR={directory} {setup} writers {action} {name} < {central_secret}")}')
        finally:
            call(f'sudo rm -f {central_secret}')
    def probe(password, tag):
        # Client-supplied host label and extra_label deliberately claim another host.
        put('host-a',client+'.bin',remote_write({'__name__':'kvm_vm_test_writer_binding','host_id':forged,'drill_id':token,'probe':tag},int(time.time()*1000)))
        put('host-a',client+'.curl',f'user = "{name}:{password}"\n'.encode())
        return t.exec_guest('host-a',f'sudo curl --config {client}.curl --cacert /etc/kvm-vm/monitoring/lab-ca.pem -sS --max-time 15 -o /dev/null -w "%{{http_code}}" -H "Content-Type: application/x-protobuf" -H "Content-Encoding: snappy" -H "X-Prometheus-Remote-Write-Version: 0.1.0" --data-binary @{client}.bin "https://172.30.0.10:9445/api/v1/write?extra_label=host_id={forged}"')
    def query(tag):
        return t.query(f'kvm_vm_test_writer_binding{{drill_id="{token}",probe="{tag}"}}')
    def check_binding(tag):
        t.until(lambda: bool(query(tag)),60,'synthetic sample ingested')
        if {r['metric'].get('host_id') for r in query(tag)} != {host_id}:
            raise lab.LabError('managed host binding not enforced by pinned backend')
    try:
        call(cmd+' init www-data')
        name = call(cmd+' stage '+host_id)
        first, second = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
        change('activate',first)
        template = (lab.ROOT/'tools/monitoring/central/nginx-managed-writers.example.conf').read_text()
        map_text = template[template.index('map $remote_user'):template.index('# The following')]
        server = '\n'.join(line[2:] for line in template.splitlines() if line.startswith('# ') and (line.startswith('#     ') or line == '# server {' or line == '# }'))
        server = server.replace('listen 443 ssl;','listen 172.30.0.10:9445 ssl;').replace('monitoring.example.com','_').replace('/path/to/fullchain.pem','/etc/nginx/kvm-lab/server.pem').replace('/path/to/privkey.pem','/etc/nginx/kvm-lab/server.key').replace('/etc/nginx/kvm-vm-writers/htpasswd',directory+'/htpasswd')
        put('central',site,(map_text+server+'\n').encode())
        call('sudo nginx -t && sudo systemctl reload nginx')
        status = probe(first,'first')
        if status not in ('200','204'): raise lab.LabError('valid writer failed')
        check_binding('first')
        change('rotate',second)
        if probe(first,'old') != '401' or probe(second,'rotated') not in ('200','204'):
            raise lab.LabError('rotation did not replace live authentication')
        check_binding('rotated')
        call(cmd+' revoke '+name)
        if probe(second,'revoked') != '401': raise lab.LabError('revoked writer accepted')
        # Allow ingestion visibility delay, then verify rejected samples absent.
        time.sleep(10)
        if query('old') or query('revoked'): raise lab.LabError('unauthorized synthetic sample exists')
        return {'status':'PASS','utc':lab.now(),'host_id':host_id,'binding_verified':True,'rotation_verified':True,'revocation_verified':True,'scope':'pinned Single and dedicated HTTPS ingress; not tenant isolation'}
    finally:
        if name:
            t.ssh('central',cmd+' revoke '+name,check=False)
        call(f'sudo rm -f {site} {central_secret}; sudo nginx -t && sudo systemctl reload nginx')
        # Remove only the test-generated registry after the listener is removed.
        call(f'sudo rm -f {directory}/htpasswd; sudo rmdir {directory} 2>/dev/null || true')
        t.exec_guest('host-a',f'sudo rm -f {client}.curl {client}.bin')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lab-id',required=True)
    print(json.dumps(exercise(lab.owned_lab(p.parse_args().lab_id)),indent=2))
