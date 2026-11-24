"""Live adapter for provisioned disposable gateways, using real HTTP APIs.

Control hook is an operator-provided executable, not part of the data path.
No TLS bypass, redirects, environment proxies, or credential logging.
"""
import base64
import json
import math
import os
from pathlib import Path
import ssl
import struct
import time
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from contract import Blocked, Reply


def varint(n):
    out = bytearray()
    while n > 127:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out) + bytes([n])


def field(n, data):
    return varint(n * 8 + 2) + varint(len(data)) + data


def remote_write(samples):
    """Remote Write 1.0 protobuf with Snappy block-format literal."""
    message = b''
    for sample in samples:
        series = b''.join(field(1, field(1, k.encode()) + field(2, v.encode())) for k,v in sorted(sample.labels.items()))
        series += field(2, b'\x09' + struct.pack('<d', sample.value) + b'\x10' + varint(sample.timestamp_ms))
        message += field(1, series)
    size = len(message) - 1
    if size < 60:
        tag = bytes([size << 2])
    else:
        width = (size.bit_length() + 7) // 8
        tag = bytes([(59 + width) << 2]) + size.to_bytes(width, 'little')
    return varint(len(message)) + tag + message


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def private_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise Blocked('fixture config must be private (0600)')
    return json.loads(path.read_text())


class HTTPAdapter:
    def __init__(self, config):
        if config.get('disposable') is not True:
            raise Blocked('explicit disposable fixture required')
        self.config = config
        self.name = config['backend']
        if self.name not in ('isolated-single', 'vm-cluster', 'mimir', 'shared-single-negative-control'):
            raise Blocked('unknown backend')
        self.timeout = float(config.get('http_timeout', 10))
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 60:
            raise Blocked('invalid HTTP timeout')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=config.get('ca_file'))))
        self.endpoints = config['endpoints']
        for key in ('write','read','oracle_a','oracle_b','oracle_internal'):
            self.url(self.endpoints[key], '')
        for key in ('write','read'):
            if urllib.parse.urlsplit(self.endpoints[key]).scheme != 'https':
                raise Blocked('public gateway must use HTTPS')
        self.hook = config['control_command']
        if not isinstance(self.hook, list) or not self.hook or not all(isinstance(x,str) and x for x in self.hook):
            raise Blocked('control command must be an argv list')
        if not Path(self.hook[0]).is_absolute():
            raise Blocked('control executable must use absolute path')

    def url(self, base, suffix):
        u = urllib.parse.urlsplit(base)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise Blocked('invalid fixture endpoint')
        if u.scheme == 'http' and u.hostname not in ('127.0.0.1','::1','localhost'):
            raise Blocked('plaintext only allowed for loopback operator oracle')
        return base.rstrip('/') + suffix

    def control(self, action, fixture):
        # Hook may dynamically provision identities; returns no secrets. Existing
        # auth file is read each request so rotation/revocation can be tested.
        result = subprocess.run(self.hook, input=json.dumps({'action':action,'fixture':fixture}), text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=False)
        if result.returncode:
            raise Blocked('fixture control failed')

    def auth(self, principal):
        records = private_json(self.config['credentials_file'])
        entry = records[principal]
        if entry.get('basic'):
            user, password = entry['basic']
            return 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()
        if entry.get('bearer'):
            return 'Bearer ' + entry['bearer']
        raise Blocked('missing principal credentials')

    def request(self, base, suffix, principal, params, body=None, attack=None):
        attack = attack or {}
        url = self.url(base, suffix)
        # Attack paths are relative to the same configured origin, never URLs.
        if 'path' in attack and (not attack['path'].startswith('/') or attack['path'].startswith('//') or '?' in attack['path'] or '#' in attack['path'] or '\\' in attack['path']):
            raise Blocked('invalid attack path')
        if 'path' in attack:
            origin = urllib.parse.urlsplit(base)
            url = urllib.parse.urlunsplit((origin.scheme,origin.netloc,attack['path'],'',''))
        params = dict(params)
        if set(attack.get('params', {})) & {'query','match[]','time','start','end','step'}:
            raise Blocked('attack may not replace test query')
        params.update(attack.get('params', {}))
        if params:
            url += '?' + urllib.parse.urlencode(params, doseq=True)
        headers = {'Authorization':self.auth(principal)}
        if body is not None:
            headers.update({'Content-Type':'application/x-protobuf','Content-Encoding':'snappy','X-Prometheus-Remote-Write-Version':'0.1.0'})
        for k,v in attack.get('headers',{}).items():
            if k.lower() in ('authorization','host','cookie','proxy-authorization'):
                raise Blocked('attack may not replace identity or origin')
            headers[k] = v
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                if body is not None:
                    return Reply(response.status)
                raw = response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:
                    raise Blocked('oversized query result')
                payload = json.loads(raw)
                if payload.get('status') != 'success' or 'data' not in payload:
                    raise Blocked('invalid Prometheus API response')
                return Reply(response.status, payload['data'])
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            return Reply(status)
        except (OSError, ValueError) as exc:
            raise Blocked('HTTP transport or response failure') from exc

    def write(self, principal, samples, attack=None):
        return self.request(self.endpoints['write'], '/api/v1/write', principal, {}, remote_write(samples), attack)

    def read(self, principal, endpoint, params, attack=None):
        return self.request(self.endpoints['read'], '/api/v1/'+endpoint, 'reader-'+principal if principal in ('a','b') else principal, params, attack=attack)

    def observe(self, scope, run_id, sample):
        # Raw range-vector query (not query_range step interpolation). Exact
        # probe ID and actual sample timestamps/values, including delayed data.
        selector = '{contract_run="%s",probe="%s"}' % (run_id,sample.labels['probe'])
        end = max(sample.timestamp_ms/1000+1, time.time())
        result = self.request(self.endpoints['oracle_'+scope], '/api/v1/query', 'oracle-'+scope, {'query':selector+'[1h]','time':str(end), 'nocache':'1'})
        if result.status != 200:
            return result
        data = result.data
        if not isinstance(data,dict) or data.get('resultType') != 'matrix' or not isinstance(data.get('result'),list):
            raise Blocked('oracle must return raw matrix')
        rows = []
        for series in data['result']:
            for ts,value in series['values']:
                rows.append({'labels':series['metric'],'timestamp_ms':round(float(ts)*1000),'value':float(value)})
        return Reply(200,rows)

    def attacks(self):
        attacks = self.config['attacks']
        for kind in ('read','write'):
            cases = attacks.get(kind, [])
            if not isinstance(cases,list) or not any('path' in a for a in cases) or not any('headers' in a for a in cases):
                raise Blocked('both path and header bypass probes are required')
        if not any('params' in a for a in attacks['write']):
            raise Blocked('write query-parameter bypass probe required')
        return attacks
