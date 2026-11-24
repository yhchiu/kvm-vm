"""Backend-neutral, opt-in synthetic tenant security acceptance contract.

No provisioner or production authorization logic here. Adapter methods must
observe the actual gateway/storage, not calculate expected results locally.
"""
from dataclasses import dataclass
import time
from datetime import datetime, timezone
import uuid

METRIC = 'kvm_vm_test_tenant_contract'
A, B, INTERNAL = 'a', 'b', 'internal'
SCOPES = (A, B, INTERNAL)
DENIED = {400, 401, 403, 404, 405, 422}


class Failure(Exception):
    pass


class Blocked(Exception):
    pass


@dataclass(frozen=True)
class Sample:
    labels: dict
    timestamp_ms: int
    value: float


@dataclass(frozen=True)
class Reply:
    status: int
    data: object = None


class Contract:
    def __init__(self, adapter, *, timeout=30, quiet=5, poll=.5, clock=time.time, sleep=time.sleep):
        self.adapter = adapter
        self.timeout, self.quiet, self.poll = timeout, quiet, poll
        self.clock, self.sleep = clock, sleep
        self.run_id = uuid.uuid4().hex
        self.marker = '{__name__="%s",contract_run="%s"}' % (METRIC, self.run_id)
        self.epoch = int(clock()) - 120
        self.fixture = {key: str(uuid.uuid4()) for key in ('host1', 'host2', 'vm_a', 'vm_b', 'vm_other', 'vm_internal', 'vm_move', 'vm_unknown')}
        self.fixture.update(run_id=self.run_id, start=self.epoch - 60, transfer=self.epoch + 30, tenant_a='a-'+self.run_id, tenant_b='b-'+self.run_id)
        self.expected = []
        self.forbidden = []
        self.seq = 0
        self.results = []

    def sample(self, vm, *, host='host1', tenant=None, at=None, extra=None):
        self.seq += 1
        labels = {'__name__': METRIC, 'contract_run': self.run_id, 'probe': f'p{self.seq}', 'host_id': self.fixture[host]}
        if vm:
            labels.update(vm_id=self.fixture[vm], vm='same-name')
        if tenant:
            labels['tenant_id'] = tenant
        labels.update(extra or {})
        return Sample(labels, self.epoch*1000+self.seq if at is None else at*1000, float(self.seq))

    def require(self, condition, message):
        if not condition:
            raise Failure(message)

    def eventually(self, callback, message):
        deadline = time.monotonic() + self.timeout
        while True:
            if callback():
                return
            if time.monotonic() >= deadline:
                raise Failure(message)
            self.sleep(self.poll)

    def observe(self, scope, sample):
        # Privileged, scope-specific *actual* storage read. No customer-facing
        # negative result is accepted as proof of absent ingestion.
        reply = self.adapter.observe(scope, self.run_id, sample)
        if reply.status != 200 or not isinstance(reply.data, list):
            raise Blocked('operator storage oracle unavailable')
        for row in reply.data:
            if not isinstance(row, dict) or not isinstance(row.get('labels'), dict):
                raise Blocked('invalid storage oracle result')
        return reply.data

    def exact(self, rows, sample):
        return [row for row in rows if row.get('timestamp_ms') == sample.timestamp_ms and row.get('value') == sample.value and row['labels'].get('contract_run') == self.run_id and row['labels'].get('probe') == sample.labels['probe']]

    def verify_scope(self, sample, scope, host):
        for partition in SCOPES:
            rows = self.observe(partition, sample)
            exact = self.exact(rows, sample)
            if partition == scope:
                self.require(len(exact) == 1 and len(rows) == 1, 'expected exactly one raw sample; missing or duplicated/rewritten')
                for row in exact:
                    self.require(row['labels'].get('__name__') == METRIC, 'metric identity changed')
                    self.require(row['labels'].get('host_id') == self.fixture[host], 'host identity not bound')
                    self.require(row['labels'].get('vm_id') == sample.labels.get('vm_id'), 'VM identity changed')
                    if 'tenant_id' in sample.labels:
                        self.require(row['labels'].get('tenant_id') != sample.labels['tenant_id'], 'untrusted tenant claim retained')
                    for key in ('vm_account_id', 'vm_project_id'):
                        if key in sample.labels:
                            self.require(row['labels'].get(key) != sample.labels[key], 'untrusted native tenant claim retained')
            else:
                self.require(not rows, 'sample leaked to another storage scope')

    def accept(self, writer, samples, routes, **kwargs):
        reply = self.adapter.write(writer, samples, **kwargs)
        self.require(200 <= reply.status < 300, 'valid write not accepted')
        for sample, (scope, host) in zip(samples, routes):
            self.eventually(lambda: bool(self.exact(self.observe(scope, sample), sample)), 'accepted write not visible in expected storage')
            self.verify_scope(sample, scope, host)
            self.expected.append((sample, scope, host))

    def reject(self, writer, sample, **kwargs):
        reply = self.adapter.write(writer, [sample], **kwargs)
        # Some remote-write gateways acknowledge a deliberately dropped sample.
        # A successful status alone does NOT establish security.
        self.require(reply.status in DENIED or 200 <= reply.status < 300, 'unexpected rejection status')
        self.forbidden.append(sample)
        self.check_absence(sample)

    def check_absence(self, sample):
        for scope in SCOPES:
            self.require(not self.observe(scope, sample), 'forbidden write found in storage')

    def query(self, reader, endpoint, params, **kwargs):
        return self.adapter.read(reader, endpoint, params, **kwargs)

    def contains(self, response, sample):
        # Query and metadata responses have different shapes. Search parsed
        # response data for the unique probe value (never response text/errors).
        def walk(obj):
            if isinstance(obj, dict):
                return any(walk(v) for v in obj.values())
            if isinstance(obj, list):
                return any(walk(v) for v in obj)
            return obj == sample.labels['probe']
        return response.status == 200 and walk(response.data)

    def read_params(self, endpoint):
        start, end = str(self.epoch - 10), str(self.epoch + 80)
        if endpoint == 'query':
            return {'query': self.marker + '[2m]', 'time': end}
        if endpoint == 'query_range':
            return {'query': self.marker, 'start': start, 'end': end, 'step': '1'}
        return {'match[]': self.marker, 'start': start, 'end': end}

    def reads(self):
        # Same exact request, A -> B -> A, including caches and enumeration.
        for endpoint in ('query', 'query_range', 'series', 'label/probe/values'):
            params = self.read_params(endpoint)
            for reader, own, other in ((A, self.sa, self.sb), (B, self.sb, self.sa), (A, self.sa, self.sb)):
                response = self.query(reader, endpoint, params)
                self.require(self.contains(response, own), 'positive customer read control failed: '+endpoint)
                self.require(not self.contains(response, other), 'cross-tenant read/cache leak: '+endpoint)
                for sample in (self.si, self.sh):
                    self.require(not self.contains(response, sample), 'operator-only metric leaked: '+endpoint)
        # Arbitrary expression and explicit victim selector, not just UI filters.
        for reader, own, other in ((A, self.sa, self.sb), (B, self.sb, self.sa)):
            selector = self.marker[:-1] + ',probe="' + other.labels['probe'] + '"}'
            params = {'query': selector + ' or ' + self.marker, 'time': str(self.epoch + 50)}
            response = self.query(reader, 'query', params)
            self.require(self.contains(response, own) and not self.contains(response, other), 'arbitrary PromQL isolation failed')
        # Metric label names and values can leak without returning sample values.
        for reader, own, other in ((A,'a','b'),(B,'b','a')):
            response = self.query(reader, 'labels', self.read_params('labels'))
            self.require(response.status == 200 and isinstance(response.data, list), 'metadata API not verifiable')
            self.require('contract_private_'+own in response.data and 'contract_private_'+other not in response.data, 'label-name enumeration leak')
            response = self.query(reader, 'label/vm_id/values', self.read_params('label/vm_id/values'))
            self.require(response.status == 200 and isinstance(response.data, list), 'VM metadata API not verifiable')
            self.require(self.fixture['vm_'+own] in response.data and self.fixture['vm_'+other] not in response.data, 'VM UUID enumeration leak')

    def seed(self):
        self.sa = self.sample('vm_a', extra={'contract_private_a':'present'})
        self.sb = self.sample('vm_b', extra={'contract_private_b':'present'})
        self.si, self.sh = self.sample('vm_internal'), self.sample(None)
        # One authenticated writer and one batch, two owners on same host.
        self.accept('host1', [self.sa, self.sb, self.si, self.sh], [(A, 'host1'), (B, 'host1'), (INTERNAL, 'host1'), (INTERNAL, 'host1')])
        other = self.sample('vm_other', host='host2')
        self.accept('host2', [other], [(A, 'host2')])

    def spoof(self):
        s = self.sample('vm_a', tenant=self.fixture['tenant_b'], extra={'vm_account_id':'999', 'vm_project_id':'999'})
        self.accept('host1', [s], [(A,'host1')])
        # Correct VM, false host label: overwrite using the credential or drop.
        s = self.sample('vm_a',host='host2')
        response = self.adapter.write('host1',[s])
        self.require(response.status in DENIED or 200 <= response.status < 300, 'unexpected spoof response')
        self.sleep(self.quiet)
        if self.observe(A,s):
            self.verify_scope(s,A,'host1')
            self.expected.append((s,A,'host1'))
        else:
            self.forbidden.append(s)
            self.check_absence(s)
        self.reject('host2', self.sample('vm_b'))  # claim host1 & its VM
        self.reject('host1', self.sample('vm_other', host='host2'))
        self.reject('host1', self.sample('vm_unknown'))
        sample = self.sample('vm_a')
        self.require(self.adapter.write('wrong',[sample]).status in (401,403), 'invalid writer credential not rejected')
        self.forbidden.append(sample)
        self.check_absence(sample)

    def bypass(self):
        attacks = self.adapter.attacks()
        for kind in ('read', 'write'):
            if not attacks.get(kind):
                raise Blocked('no backend-specific '+kind+' bypass probes configured')
        for attack in attacks['read']:
            params = self.read_params('query')
            response = self.query(A, 'query', params, attack=attack)
            self.require(response.status in DENIED or (response.status == 200 and self.contains(response, self.sa) and all(not self.contains(response, sample) for sample in (self.sb,self.si,self.sh))), 'tenant path/header bypass or unexpected response')
        for attack in attacks['write']:
            s = self.sample('vm_a', tenant=self.fixture['tenant_b'])
            response = self.adapter.write('host1', [s], attack=attack)
            self.require(response.status in DENIED or 200 <= response.status < 300, 'unexpected write attack response')
            # If normalized/accepted, it must be in A only. If denied/dropped,
            # no sample should appear later. Allow async ingress to settle.
            self.sleep(self.quiet)
            if self.observe(A, s):
                self.verify_scope(s,A,'host1')
                self.expected.append((s,A,'host1'))
            else:
                self.forbidden.append(s)
                self.check_absence(s)
        response = self.query('wrong', 'query', self.read_params('query'))
        self.require(response.status in (401,403), 'invalid reader credential not rejected')
        sample = self.sample('vm_a')
        response = self.adapter.write('reader-a', [sample])
        self.require(response.status in (401,403), 'reader can write or auth failed unexpectedly')
        self.forbidden.append(sample)
        self.check_absence(sample)
        response = self.query('host1', 'query', self.read_params('query'))
        self.require(response.status in (401,403), 'writer can read or auth failed unexpectedly')

    def transfer(self):
        before = self.sample('vm_move', at=self.epoch+20)
        self.accept('host1',[before],[(A,'host1')])
        self.adapter.control('transfer', self.fixture)
        boundary = self.sample('vm_move', at=self.epoch+30)
        self.accept('host1',[boundary],[(B,'host1')])
        after = self.sample('vm_move', at=self.epoch+40)
        self.accept('host1',[after],[(B,'host1')])
        old = self.sample('vm_move', at=self.epoch+29)
        reply = self.adapter.write('host1',[old])
        self.require(reply.status in DENIED or 200 <= reply.status < 300, 'unexpected delayed write status')
        self.sleep(self.quiet)
        if self.observe(A,old):
            self.verify_scope(old,A,'host1')
            self.expected.append((old,A,'host1'))
        else:
            self.forbidden.append(old)
            self.check_absence(old)
        for reader, own, other in ((A,before,after),(B,after,before)):
            result = self.query(reader,'query',self.read_params('query'))
            self.require(self.contains(result,own) and not self.contains(result,other), 'historical ownership leak or data inaccessible')

    def unavailable(self):
        self.adapter.control('policy-down',self.fixture)
        sample = self.sample('vm_a')
        try:
            reply = self.adapter.write('host1',[sample])
            self.require(reply.status in DENIED or reply.status in (502,503,504) or 200 <= reply.status < 300, 'unexpected policy failure status')
            self.forbidden.append(sample)
            self.check_absence(sample)
        finally:
            self.adapter.control('policy-up',self.fixture)
        recovery = self.sample('vm_a')
        self.accept('host1',[recovery],[(A,'host1')])

    def rotate(self):
        self.adapter.control('rotate-writer', self.fixture)
        sample = self.sample('vm_a')
        self.require(self.adapter.write('host1-old',[sample]).status in (401,403), 'superseded credential not rejected')
        self.forbidden.append(sample)
        self.check_absence(sample)
        self.accept('host1',[self.sample('vm_a')],[(A,'host1')])

    def revoke(self):
        self.adapter.control('revoke', self.fixture)
        self.reject('host1',self.sample('vm_a'))
        sample = self.sample('vm_b')
        self.require(self.adapter.write('host1',[sample]).status in (401,403), 'revoked writer not explicitly rejected')
        self.forbidden.append(sample)
        self.check_absence(sample)
        self.accept('host2',[self.sample('vm_other',host='host2')],[(A,'host2')])

    def revoke_reader(self):
        self.adapter.control('revoke-reader', self.fixture)
        response = self.query(A, 'query', self.read_params('query'))
        self.require(response.status in (401,403), 'revoked reader not rejected')
        response = self.query(B, 'query', self.read_params('query'))
        self.require(self.contains(response,self.sb) and not self.contains(response,self.sa), 'remaining reader unavailable or leaked')

    def final_check(self):
        deadline = time.monotonic() + self.quiet
        while True:
            for sample in self.forbidden:
                self.check_absence(sample)
            for sample, scope, host in self.expected:
                self.verify_scope(sample,scope,host)
            if time.monotonic() >= deadline:
                break
            self.sleep(self.poll)

    def run(self):
        scenarios = [('mixed-host-batch',self.seed),('read-isolation-cache-enumeration',self.reads),('spoof-and-unknown',self.spoof),('api-bypass-and-role-separation',self.bypass),('ownership-transfer-delayed-sample',self.transfer),('policy-outage-fail-closed',self.unavailable),('writer-rotation',self.rotate),('credential-revocation',self.revoke),('reader-revocation',self.revoke_reader),('final-storage-recheck',self.final_check)]
        started = datetime.now(timezone.utc).isoformat()
        stop = False
        try:
            self.adapter.control('prepare', self.fixture)
        except Exception as exc:
            self.results.append({'scenario':'prepare','status':'BLOCKED','reason':type(exc).__name__})
            stop = True
        try:
            for name, fn in scenarios:
                if stop:
                    self.results.append({'scenario':name,'status':'NOT_RUN','reason':'prerequisite failed'})
                    continue
                start = time.monotonic()
                try:
                    fn()
                    self.results.append({'scenario':name,'status':'PASS'})
                except Failure as exc:
                    self.results.append({'scenario':name,'status':'FAIL','reason':str(exc)})
                    stop = True
                except Exception as exc:
                    # Adapter exception text may contain credentials/URLs.
                    self.results.append({'scenario':name,'status':'BLOCKED','reason':type(exc).__name__})
                    stop = True
                self.results[-1]['duration_seconds'] = round(time.monotonic()-start,3)
        finally:
            try:
                self.adapter.control('cleanup',self.fixture)
            except Exception:
                self.results.append({'scenario':'cleanup','status':'BLOCKED'})
        return {'schema':1,'run_id':self.run_id,'backend':self.adapter.name,'scope':'synthetic tenant contract; not production readiness','started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),'fixture':self.fixture,'negative_observation_seconds':self.quiet,'accepted_probes':[{
            'probe':s.labels['probe'],'timestamp_ms':s.timestamp_ms,'value':s.value,'scope':scope,'host_id':self.fixture[host]} for s,scope,host in self.expected],
            'forbidden_probes':[{'probe':s.labels['probe'],'timestamp_ms':s.timestamp_ms,'value':s.value} for s in self.forbidden],'results':self.results,'status':'PASS' if self.results and all(r['status']=='PASS' for r in self.results) else ('FAIL' if any(r['status']=='FAIL' for r in self.results) else 'BLOCKED')}
