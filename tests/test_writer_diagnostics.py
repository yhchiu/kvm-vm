import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('diagnose_writer', ROOT/'tools/monitoring/diagnose-writer.py')
d = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(d)


def test_allowlisted_metrics_no_labels_and_unknown_not_zero():
    metrics = d.selected('''# HELP foo text
vmagent_remotewrite_pending_data_bytes{url="secret",path="/queue a"} 1.25e+2
vmagent_remotewrite_pending_data_bytes{url="another"} 75
vmagent_remotewrite_packets_dropped_total 0
vmagent_remotewrite_samples_dropped_total NaN
vmagent_remotewrite_push_failures_total -1
foo{password="secret"} 1
''', d.AGENT)
    assert metrics['vmagent_remotewrite_pending_data_bytes'] == 200
    assert metrics['vmagent_remotewrite_packets_dropped_total'] == 0
    assert metrics['vmagent_remotewrite_samples_dropped_total'] is None
    assert metrics['vmagent_remotewrite_push_failures_total'] is None
    assert 'secret' not in json.dumps(metrics)


def test_agent_and_hybrid_diagnostics(tmp_path):
    (tmp_path/'remote.env').write_text('REMOTE_DISK_QUEUE_LIMIT=536870912\n')
    (tmp_path/'.env').write_text('PROMETHEUS_PORT=9190\n')
    calls = []
    def scrape(port, wanted):
        calls.append(port)
        values = {key: None for key in wanted}
        if port == 8429: values['vmagent_remotewrite_pending_data_bytes'] = 268435456
        if port == 9190:
            values['prometheus_remote_storage_queue_highest_timestamp_seconds'] = 1200
            values['prometheus_remote_storage_queue_highest_sent_timestamp_seconds'] = 1000
        return {'reachable': port != 9179, 'metrics': values}
    agent = d.diagnose(tmp_path,'agent',scrape)
    assert agent['pending_to_configured_limit_ratio'] == .5
    assert not agent['exporter']['reachable']
    assert d.diagnose(tmp_path,'hybrid',scrape)['estimated_send_lag_seconds'] == 200
    assert calls == [8429,9179,9190,9179]


def test_queue_limit_explicit_and_preserved(tmp_path):
    p = tmp_path/'password'; p.write_text('example-secret\n')
    cfg = tmp_path/'cfg'
    env = {**os.environ, 'KVM_VM_REMOTE_WRITE_URL':'https://example.com/api/v1/write',
           'KVM_VM_REMOTE_PASSWORD_FILE':str(p), 'PROMETHEUS_PORT':'9190'}
    env.pop('KVM_VM_REMOTE_DISK_QUEUE_LIMIT',None); env.pop('KVM_VM_REMOTE_RECONFIGURE',None)
    cmd = [sys.executable, str(ROOT/'tools/monitoring/configure-remote.py'), str(cfg)]
    def run(*args): return subprocess.run(cmd+list(args),env=env,capture_output=True,text=True,timeout=10)
    assert run('--disk-queue-limit','0').returncode != 0
    assert not cfg.exists()
    assert run('--disk-queue-limit','536870912').returncode == 0
    assert 'REMOTE_DISK_QUEUE_LIMIT=536870912' in (cfg/'remote.env').read_text()
    assert run('--disk-queue-limit','1073741824').returncode != 0
    assert run('--reconfigure').returncode == 0
    assert 'REMOTE_DISK_QUEUE_LIMIT=536870912' in (cfg/'remote.env').read_text()
    assert run('--reconfigure','--disk-queue-limit','1073741824').returncode == 0
    assert 'REMOTE_DISK_QUEUE_LIMIT=1073741824' in (cfg/'remote.env').read_text()
    conf = json.loads((cfg/'prometheus-hybrid.yml').read_text())
    job = next(x for x in conf['scrape_configs'] if x['job_name']=='prometheus-writer')
    assert job['static_configs'][0]['targets']==['127.0.0.1:9190']
    assert conf['remote_write'][0]['queue_config']['retry_on_http_429'] is True


def test_central_self_monitoring_is_internal():
    central = ROOT/'tools/monitoring/central'
    scrape = yaml.safe_load((central/'inventory/scrape.yml').read_text())
    job = next(x for x in scrape['scrape_configs'] if x['job_name']=='kvm-vm-central-components')
    assert job['static_configs'][0]['targets']==['victoriametrics:8428','vmalert:8880','alertmanager:9093']
    rules = yaml.safe_load((central/'rules/rules.yml').read_text())
    names = {r['alert'] for g in rules['groups'] for r in g['rules']}
    assert {'KvmVmAgentQueueDataDiscarded','KvmVmHybridWriteFailed','KvmVmCentralComponentScrapeFailed'} <= names


def test_replace_password_preserves_identity_and_refuses_running_marker(tmp_path):
    secret = tmp_path/'old'; secret.write_text('old-password\n')
    cfg = tmp_path/'cfg'
    env = {**os.environ, 'KVM_VM_REMOTE_WRITE_URL':'https://example.com/api/v1/write',
           'KVM_VM_REMOTE_PASSWORD_FILE':str(secret)}
    cmd = [sys.executable, str(ROOT/'tools/monitoring/configure-remote.py'), str(cfg)]
    assert subprocess.run(cmd, env=env,capture_output=True,timeout=10).returncode == 0
    identity = (cfg/'remote.json').read_bytes()
    before = (cfg/'remote.env').read_bytes()
    def replace(password):
        return subprocess.run(cmd+['--replace-password'], env=env,input=password,text=True,capture_output=True,timeout=10)
    (cfg/'.mode.pending').write_text('agent\n')
    assert replace('new-long-password-123456\n').returncode != 0
    (cfg/'.mode.pending').unlink()
    assert replace('bad\n').returncode != 0
    assert (cfg/'remote-password').read_text()=='old-password\n'
    result=replace('new-long-password-123456\n')
    assert result.returncode == 0 and 'new-long-password' not in result.stdout+result.stderr
    assert (cfg/'remote-password').read_text()=='new-long-password-123456\n'
    assert (cfg/'remote.json').read_bytes()==identity and (cfg/'remote.env').read_bytes()==before
