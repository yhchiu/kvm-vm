#!/usr/bin/env python3
"""Opt-in long outage drill on an already-owned multi-NAT lab, never production."""
import argparse
import json
import math
import shlex
import signal
import time

import lab

CONFIG = '/etc/kvm-vm/monitoring'
SETUP = '/opt/kvm-vm/tools/setup-monitoring.sh'


def coverage(samples, start, end, max_gap):
    # Count only original timestamps (milliseconds), not query-range evaluation ticks.
    stamps = sorted({ts / 1000 for ts in samples if start*1000 <= ts <= end*1000})
    edges = [start, *stamps, end]
    gap = max((b-a for a,b in zip(edges, edges[1:])), default=end-start)
    return {'samples': len(stamps), 'max_gap_seconds': round(gap,3),
            'within_gap_budget': bool(stamps) and gap <= max_gap}


def raw_samples(t, uid, start, end):
    result = set()
    for left in range(int(start), math.ceil(end), 3600):
        right = min(left+3600, math.ceil(end))
        matcher = f'match[]=kvm_vm_collector_success{{host_id="{uid}"}}'
        cmd = ('curl -fsSG --max-time 30 --data-urlencode '+shlex.quote(matcher)+
               f' --data-urlencode start={left} --data-urlencode end={right} http://127.0.0.1:8428/api/v1/export')
        for line in t.exec_guest('central', cmd, timeout=40).splitlines():
            row = json.loads(line)
            if row['metric'].get('host_id') != uid:
                raise lab.LabError('unexpected host in export')
            result.update(ts for ts,val in zip(row['timestamps'],row['values']) if val == 1)
    return result


def diagnose(t, host, mode):
    return json.loads(t.exec_guest(host, f'sudo {SETUP} diagnostics --mode {mode}'))


def exercise(t, args):
    host = args.host
    mode = 'agent' if host == 'host-a' else 'hybrid'
    uid = json.loads(t.exec_guest(host, f'sudo cat {CONFIG}/remote.json'))['host_id']
    t.wait_for_writer(host, mode, uid)
    router = host.replace('host-', 'router-')
    if t.ssh(router, 'sudo iptables -C FORWARD -d 172.30.0.10 -p tcp --dport 443 -j REJECT', check=False).returncode != 1:
        raise lab.LabError('existing outage rule or firewall error; heal the lab before drilling')
    baseline = diagnose(t, host, mode)
    points = [baseline]
    killed = False
    start = time.time()
    try:
        t.fault(host, 'disconnect')
        deadline = time.monotonic()+args.seconds
        while time.monotonic() < deadline:
            time.sleep(min(60, max(0, deadline-time.monotonic())))
            points.append(diagnose(t,host,mode))
            if args.kill_writer and not killed and time.time()-start >= args.seconds/2:
                if mode == 'agent':
                    compose = f'sudo docker compose --project-directory {CONFIG} --env-file {CONFIG}/remote.env -f {CONFIG}/compose.agent.yaml'
                    service = 'vmagent'
                else:
                    compose = f'sudo docker compose --project-directory {CONFIG} --env-file {CONFIG}/.env --env-file {CONFIG}/remote.env -f {CONFIG}/compose.yaml -f {CONFIG}/compose.hybrid.yaml'
                    service = 'prometheus'
                t.exec_guest(host, f'{compose} kill -s SIGKILL {service} && {compose} up -d {service}', timeout=180)
                # An interrupted writer can lose in-memory samples. Test and report, don't assume.
                killed = True
    finally:
        end = time.time()
        # Remove only this drill's rule; preserve any other lab netem policy.
        t.exec_guest(router, 'sudo iptables -D FORWARD -d 172.30.0.10 -p tcp --dport 443 -j REJECT 2>/dev/null || true')
    t.wait_for_writer(host,mode,uid)
    # Wait for historical replay; positive fresh samples alone are not evidence.
    result = None
    recovery_deadline = time.monotonic()+args.recovery_timeout
    while True:
        result = coverage(raw_samples(t,uid,start,end),start,end,args.max_gap)
        if result['within_gap_budget'] or time.monotonic() >= recovery_deadline:
            break
        time.sleep(30)
    final = diagnose(t,host,mode)
    pending_metric = 'vmagent_remotewrite_pending_data_bytes' if mode=='agent' else 'prometheus_remote_storage_samples_pending'
    pending = [p['writer']['metrics'].get(pending_metric) for p in points]
    report = {'status': 'PASS' if result['within_gap_budget'] else 'LOSS_OR_DELAY_OBSERVED' if args.allow_loss else 'FAIL',
              'utc':lab.now(),'host_id':uid,'mode':mode,'outage_start':start,'outage_end':end,
              'recovery_observed_seconds':round(time.time()-end,3),'writer_killed':killed,
              'coverage':result,'max_gap_budget_seconds':args.max_gap,
              'peak_observed_pending':max((p for p in pending if p is not None),default=None),
              'pending_metric':pending_metric,'initial_diagnostics':baseline,'final_diagnostics':final,
              'diagnostic_samples':points}
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lab-id', required=True)
    p.add_argument('--host', choices=lab.HOSTS, default='host-a')
    p.add_argument('--seconds', type=int, default=3600)
    p.add_argument('--recovery-timeout', type=int, default=900)
    p.add_argument('--max-gap', type=int, default=45)
    p.add_argument('--kill-writer', action='store_true')
    p.add_argument('--allow-loss', action='store_true', help='characterization only; loss is still explicitly reported')
    args = p.parse_args()
    if not 60 <= args.seconds <= 259200 or not 60 <= args.recovery_timeout <= 86400 or not 15 <= args.max_gap <= 300:
        p.error('invalid outage, recovery or gap budget')
    # Run finally on normal termination. SIGKILL/power loss requires manual lab heal.
    def interrupted(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    report = exercise(lab.owned_lab(args.lab_id),args)
    print(json.dumps(report, indent=2))
    return 1 if report['status']=='FAIL' else 0


if __name__ == '__main__':
    raise SystemExit(main())
