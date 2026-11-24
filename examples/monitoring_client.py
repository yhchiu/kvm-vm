#!/usr/bin/env python3
"""Server-side reference client; import from a trusted VM Panel, never a browser.

Authorization, history and rate limiting are supplied by the Panel. This file
is not an API server, a tenant gateway, or an independent identity authority.
"""
from dataclasses import dataclass
import base64
import json
import math
from pathlib import Path
import ssl
import time
import urllib.parse
import urllib.request
import uuid

MAX_RANGE = 30 * 86400
MAX_POINTS = 1000
MAX_INTERVALS = 16
MAX_BODY = 4 * 1024 * 1024
RATE_WINDOW = 300
GAUGE_WINDOW = 60
# Bound on source timestamp uncertainty (collector cache, balloon stats, clocks).
# An operator must verify this bound on all hosts before customer integration.
SOURCE_GUARD = 180
CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'tools/monitoring/queries/v1.json').read_text())['metrics']


class AccessDenied(Exception):
    """No current grant or quota; never reveal whether a VM exists."""


class InvalidRequest(ValueError):
    pass


class BackendError(Exception):
    pass


@dataclass(frozen=True)
class Placement:
    host_id: str
    vm_id: str
    start: int   # authorized interval, inclusive UTC seconds
    end: int | None = None  # exclusive; None means still authorized


@dataclass(frozen=True)
class Grant:
    revision: str
    placements: tuple[Placement, ...]


def canonical_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def validate_grant(grant):
    if not isinstance(grant, Grant) or not isinstance(grant.revision, str) or not grant.revision or not isinstance(grant.placements, tuple) or not 1 <= len(grant.placements) <= MAX_INTERVALS:
        raise AccessDenied('no valid authorization')
    prev = None
    for p in grant.placements:
        if (not isinstance(p, Placement) or not canonical_uuid(p.host_id) or not canonical_uuid(p.vm_id)
                or type(p.start) is not int or p.start < 0
                or (p.end is not None and (type(p.end) is not int or p.end <= p.start))
                or (prev is not None and (prev.end is None or p.start < prev.end))):
            raise AccessDenied('invalid authorization history')
        prev = p
    return grant


def expression(metric, placement, engine='victoriametrics'):
    if metric not in CATALOG or not canonical_uuid(placement.host_id) or not canonical_uuid(placement.vm_id):
        raise InvalidRequest('unsupported metric or identity')
    if engine not in ('victoriametrics', 'prometheus'):
        raise InvalidRequest('unsupported query engine')
    spec = CATALOG[metric]
    scope = '{host_id="%s",vm_id="%s"}' % (placement.host_id, placement.vm_id)
    source = 'kvm_vm_domain_' + spec['source'] + scope
    if spec['kind'] == 'gauge':
        return f'last_over_time({source}[{GAUGE_WINDOW}s])', GAUGE_WINDOW
    # MetricsQL rate() also reads the sample immediately before the range.
    # Use its Prometheus variant; never silently fall back to plain rate().
    rate = 'rate_prometheus' if engine == 'victoriametrics' else 'rate'
    query = f'{rate}({source}[{RATE_WINDOW}s])'
    if spec['kind'] == 'cpu_ratio':
        denominator = f'last_over_time(kvm_vm_domain_vcpus{scope}[{GAUGE_WINDOW}s])'
        query += f' / on(host_id,vm_id,vm) ({denominator} > 0)'
    return query, RATE_WINDOW


def plan(placement, start, end, step, lookback):
    # All output points stay on the same request grid, but each query reads a
    # single authorized Host/VM placement. No implicit lookback or offset.
    low = max(start, placement.start + SOURCE_GUARD + lookback)
    high = end if placement.end is None else min(end, placement.end - SOURCE_GUARD - 1)
    first = start + max(0, (low-start+step-1)//step)*step
    last = start + (high-start)//step*step
    return (first,last) if first <= last else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class QueryClient:
    def __init__(self, base_url, username, password, *, ca_file=None, engine='victoriametrics'):
        u = urllib.parse.urlsplit(base_url)
        if (u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment
                or u.path not in ('', '/') or not isinstance(username, str) or not username or ':' in username
                or not isinstance(password, str) or not password or any(c in username+password for c in '\r\n')):
            raise InvalidRequest('require trusted HTTPS origin and service credential')
        if engine not in ('victoriametrics','prometheus'):
            raise InvalidRequest('unsupported query engine')
        self.engine = engine
        self.url = base_url.rstrip('/') + '/api/v1/query_range'
        self.auth = 'Basic ' + base64.b64encode((username+':'+password).encode()).decode('ascii')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def range(self, query, start, end, step):
        # Trusted internal method, not callable directly by a customer.
        params = {'query':query,'start':start,'end':end,'step':step,'timeout':'10s'}
        if self.engine == 'victoriametrics':
            params['nocache'] = '1'
        req = urllib.request.Request(self.url+'?'+urllib.parse.urlencode(params), headers={'Authorization':self.auth,'Accept':'application/json'})
        try:
            with self.opener.open(req,timeout=10) as r:
                raw = r.read(MAX_BODY+1)
                if r.status != 200 or len(raw)>MAX_BODY:
                    raise BackendError('invalid backend response')
            result = json.loads(raw)
            if result.get('status') != 'success' or result.get('warnings') or result.get('isPartial') is True:
                raise BackendError('incomplete backend response')
            data = result['data']
            if data.get('resultType') != 'matrix' or not isinstance(data['result'],list):
                raise BackendError('expected matrix')
            return data['result']
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as exc:
            # Do not surface URLs, raw query, upstream errors or credentials.
            raise BackendError('monitoring backend unavailable or invalid') from None


def collect(rows, metric, p, first, last, step):
    """Check scope/grid and strip all infrastructure labels before returning.

    Device totals sum only the observed series; duplicated device samples
    (e.g. overlapping rename or duplicate writers) fail rather than double count.
    """
    if not isinstance(rows,list):
        raise BackendError('invalid matrix')
    sums, seen = {}, set()
    device = CATALOG[metric].get('device')
    try:
        for row in rows:
            labels = row['metric']
            if labels.get('host_id') != p.host_id or labels.get('vm_id') != p.vm_id:
                raise BackendError('unexpected scope in backend result')
            key = labels.get(device) if device else ''
            if not isinstance(key,str) or (device and not key):
                raise BackendError('missing device identity')
            for ts, raw in row['values']:
                if type(ts) not in (int,float) or not math.isfinite(ts) or int(ts)!=ts or not first<=ts<=last or (ts-first)%step:
                    raise BackendError('unexpected evaluation timestamp')
                if (key,ts) in seen:
                    raise BackendError('ambiguous overlapping series')
                seen.add((key,ts))
                value = float(raw)
                if not math.isfinite(value):
                    continue  # unsupported/NaN becomes a gap, not zero
                if value < 0:
                    raise BackendError('negative resource metric')
                sums[ts] = sums.get(ts,0.0)+value
                if not math.isfinite(sums[ts]):
                    raise BackendError('invalid aggregate')
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError):
        raise BackendError('invalid backend matrix') from None
    return sums


class PanelMonitoring:
    def __init__(self, client, authorize, admit, *, clock=time.time):
        # authorize(authenticated_principal, opaque_service_id) returns Grant
        # or None. admit(principal, service_id) applies external quota/concurrency.
        self.client,self.authorize,self.admit,self.clock=client,authorize,admit,clock

    def grant(self, principal, service_id):
        try:
            return validate_grant(self.authorize(principal, service_id))
        except Exception:
            raise AccessDenied('no valid authorization') from None

    def read(self, principal, service_id, metric, start, end, step=None):
        if not isinstance(metric,str) or metric not in CATALOG or type(start) is not int or type(end) is not int or not 0<=start<=end<=int(self.clock()) or end-start>MAX_RANGE:
            raise InvalidRequest('invalid metric or time range')
        step = max(15,(end-start+MAX_POINTS-2)//(MAX_POINTS-1)) if step is None else step
        if type(step) is not int or not 15<=step<=MAX_RANGE or (end-start)//step+1>MAX_POINTS:
            raise InvalidRequest('invalid resolution')
        grant = self.grant(principal,service_id)
        try:
            allowed = self.admit(principal,service_id) is True
        except Exception:
            allowed = False
        if not allowed:
            raise AccessDenied('query budget exhausted')
        output = {}
        deadline = time.monotonic()+20
        for placement in grant.placements:
            query, lookback = expression(metric,placement,self.client.engine)
            bounds = plan(placement,start,end,step,lookback)
            if not bounds:
                continue
            if time.monotonic() >= deadline:
                raise BackendError('query budget exceeded')
            rows = self.client.range(query,*bounds,step)
            if time.monotonic() >= deadline:
                raise BackendError('query budget exceeded')
            output.update(collect(rows,metric,placement,*bounds,step))
        # Re-resolve at return time. Revision MUST include all ownership and
        # ACL changes; no shared response cache in this reference client.
        if self.grant(principal,service_id) != grant:
            raise AccessDenied('authorization changed during query')
        return {'schema_version':1,'metric':metric,'unit':CATALOG[metric]['unit'],'step':step,
                'points':[[t,output.get(t)] for t in range(start,end+1,step)]}
