"""Reference Panel query security; no real TSDB or production user data."""
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_monitoring_example', ROOT/'examples/monitoring_client.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
H1, H2, V1, V2 = [f'{i:08d}-1111-4111-8111-111111111111' for i in range(1,5)]
P = m.Placement(H1,V1,1000,2000)


class Backend:
    engine = 'victoriametrics'
    def __init__(self):
        self.calls=[]
        self.rows=[]
    def range(self,*args):
        self.calls.append(args)
        return self.rows


def panel(backend, grant=m.Grant('rev1',(P,)), **kwargs):
    return m.PanelMonitoring(backend,kwargs.get('authorize',lambda user,svc: grant if (user,svc)==('a','service-a') else None),kwargs.get('admit',lambda *_:True),clock=lambda:10000000)


def row(p=P, points=None, **labels):
    return {'metric':{'host_id':p.host_id,'vm_id':p.vm_id,'host':'secret-host','vm':'secret-name',**labels},'values':points or [[1480,'2']]}


def test_catalog_matches_exporter_and_safe_query_templates():
    exporter=(ROOT/'kvm-vm').read_text()
    assert len(m.CATALOG)==18
    for name, item in m.CATALOG.items():
        assert f'"kvm_vm_domain_{item["source"]}"' in exporter
        for engine in ('victoriametrics','prometheus'):
            query, lookback=m.expression(name,P,engine)
            assert f'host_id="{H1}",vm_id="{V1}"' in query
            assert 'offset' not in query and '=~' not in query and '[5m]' not in query
            assert lookback in (60,300)
            if item['kind']!='gauge':
                assert query.startswith('rate_prometheus(' if engine=='victoriametrics' else 'rate(')
    assert 'on(host_id,vm_id,vm)' in m.expression('cpu_ratio',P)[0]


@pytest.mark.parametrize('identity',[None,'web01','a"} or up','00000000-0000-0000-0000-00000000000Z'])
def test_invalid_identity_rejected(identity):
    with pytest.raises(m.AccessDenied): m.validate_grant(m.Grant('x',(m.Placement(H1,identity,1000),)))


@pytest.mark.parametrize('metric,start,end,step',[
    ('up',1000,2000,15),('cpu_cores or up',1000,2000,15),('cpu_cores',2000,1000,15),
    ('cpu_cores',0,31*86400,3600),('cpu_cores',1000,10000001,3600),
    ('cpu_cores',0,1000000,15),('cpu_cores',1000,2000,True),('cpu_cores',1000,2000,0),
    ('cpu_cores',1000.0,2000,15)])
def test_request_validation_before_backend(metric,start,end,step):
    b=Backend()
    with pytest.raises(m.InvalidRequest): panel(b).read('a','service-a',metric,start,end,step)
    assert not b.calls


def test_bola_and_quota_fail_before_backend():
    b=Backend(); p=panel(b)
    for args in [('b','service-a'),('a','service-b')]:
        with pytest.raises(m.AccessDenied):p.read(*args,'cpu_cores',1000,2000,15)
    with pytest.raises(m.AccessDenied):panel(b,admit=lambda *_:False).read('a','service-a','cpu_cores',1000,2000,15)
    assert not b.calls


def test_history_guard_and_migration_no_gap_bridging():
    b=Backend()
    grant=m.Grant('r',(P,m.Placement(H2,V1,2000,3000),m.Placement(H2,V2,4000,None)))
    result=panel(b,grant).read('a','service-a','cpu_cores',1000,5000,20)
    assert [(x[1],x[2],x[3]) for x in b.calls]==[(1480,1800,20),(2480,2800,20),(4480,5000,20)]
    assert H1 in b.calls[0][0] and V1 in b.calls[1][0] and H2 in b.calls[1][0] and V2 in b.calls[2][0]
    assert all(v is None for _,v in result['points'])


def test_short_ownership_and_overlap_fail_closed():
    b=Backend(); p=panel(b,m.Grant('r',(m.Placement(H1,V1,1000,1200),)))
    assert all(v is None for _,v in p.read('a','service-a','cpu_cores',1000,1200,20)['points'])
    assert not b.calls
    with pytest.raises(m.AccessDenied):panel(b,m.Grant('r',(P,m.Placement(H2,V1,1900),))).read('a','service-a','cpu_cores',1000,2500,20)


def test_ownership_revoked_mid_request_and_no_cache():
    b=Backend(); grants=iter([m.Grant('r',(P,)),None])
    p=panel(b,authorize=lambda *_:next(grants))
    with pytest.raises(m.AccessDenied):p.read('a','service-a','cpu_cores',1000,2000,20)
    assert len(b.calls)==1
    p=panel(b); p.read('a','service-a','cpu_cores',1000,2000,20);p.read('a','service-a','cpu_cores',1000,2000,20)
    assert len(b.calls)==3


def test_device_totals_missing_and_no_infrastructure_labels():
    b=Backend();b.rows=[row(disk='vda',points=[[1480,'2'],[1500,'NaN']]),row(disk='vdb',points=[[1480,'3']])]
    result=panel(b).read('a','service-a','disk_read',1000,2000,20)
    points=dict(result['points']);assert points[1480]==5 and points[1500] is None and points[1000] is None
    assert result['unit']=='bytes/s' and H1 not in json.dumps(result) and 'secret-host' not in json.dumps(result) and 'vda' not in json.dumps(result)


@pytest.mark.parametrize('rows',[
    [row(m.Placement(H2,V1,1000))], [row(m.Placement(H1,V2,1000))],
    [row(points=[[1479,'1']])], [row(points=[[1820,'1']])], [row(points=[[1481,'1']])],
    [row(),row(vm='newname')], [row(points=[[1480,'-1']])], [{'metric':{},'values':[]}],
    [{'metric':{'host_id':H1,'vm_id':V1},'values':[[1480,'invalid']]}]])
def test_reject_wrong_scope_time_duplicate_or_malformed_result(rows):
    b=Backend();b.rows=rows
    with pytest.raises(m.BackendError):panel(b).read('a','service-a','cpu_cores',1000,2000,20)


def test_automatic_step_bounds():
    b=Backend(); p=panel(b,m.Grant('r',(m.Placement(H1,V1,0),)))
    result=p.read('a','service-a','cpu_cores',100000,100000+30*86400)
    assert len(result['points'])<=1000 and result['step']>=15


@pytest.mark.parametrize('url',['http://localhost','https://user:pass@localhost','https://localhost/path','https://localhost?query=up'])
def test_client_only_trusted_https_origin(url):
    with pytest.raises(m.InvalidRequest):m.QueryClient(url,'panel','secret')


def test_policy_and_quota_service_errors_fail_closed():
    b=Backend()
    def broken(*args):raise RuntimeError('private database URL')
    for kwargs in ({'authorize':broken},{'admit':broken}):
        with pytest.raises(m.AccessDenied) as err:
            panel(b,**kwargs).read('a','service-a','cpu_cores',1000,2000,20)
        assert 'private' not in str(err.value)
    assert not b.calls
