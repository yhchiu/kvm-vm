#!/usr/bin/env python3
"""Local writer diagnostics; available even when Central is unreachable."""
import argparse
import json
import math
from pathlib import Path
import re
import sys
import time
import urllib.request

AGENT = {
    'vmagent_remotewrite_pending_data_bytes': 'sum',
    'vm_persistentqueue_bytes_dropped_total': 'sum',
    'vmagent_remotewrite_samples_dropped_total': 'sum',
    'vmagent_remotewrite_packets_dropped_total': 'sum',
    'vmagent_remotewrite_push_failures_total': 'sum',
}
HYBRID = {
    'prometheus_remote_storage_samples_pending': 'sum',
    'prometheus_remote_storage_samples_failed_total': 'sum',
    'prometheus_remote_storage_samples_dropped_total': 'sum',
    'prometheus_remote_storage_samples_retried_total': 'sum',
    'prometheus_remote_storage_queue_highest_timestamp_seconds': 'max',
    'prometheus_remote_storage_queue_highest_sent_timestamp_seconds': 'min',
}
EXPORTER = {'kvm_vm_collector_success': 'min', 'kvm_vm_collector_last_success_timestamp_seconds': 'max'}
# Explicit names, valid numeric value, optional timestamp. Never expose labels.
SAMPLE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?:[^"{}\\]|"(?:[^"\\]|\\.)*")*\})?\s+(\S+)(?:\s+\S+)?\s*$')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def selected(text, wanted):
    values = {key: [] for key in wanted}
    for line in text.splitlines():
        match = SAMPLE.fullmatch(line)
        if match and match[1] in wanted:
            try:
                value = float(match[2])
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0:
                values[match[1]].append(value)
    result = {key: (None if not rows else {'sum': sum, 'min': min, 'max': max}[wanted[key]](rows))
              for key, rows in values.items()}
    return {key: value if value is not None and math.isfinite(value) else None for key, value in result.items()}


def fetch(port, wanted):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}/metrics', timeout=5) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if response.status != 200 or len(raw) > 8 * 1024 * 1024:
                raise ValueError('invalid metrics response')
        return {'reachable': True, 'metrics': selected(raw.decode(), wanted)}
    except (OSError, ValueError):
        return {'reachable': False, 'metrics': {key: None for key in wanted}}


def config_number(path, key, default, low, high):
    values = [row.split('=', 1)[1] for row in path.read_text().splitlines() if row.startswith(key+'=')]
    if not values:
        return default
    if len(values) != 1 or not re.fullmatch(r'[1-9][0-9]{0,12}', values[0]) or not low <= int(values[0]) <= high:
        raise ValueError(f'invalid {key}')
    return int(values[0])


def diagnose(directory, mode, scrape=fetch):
    port = 8429 if mode == 'agent' else config_number(directory/'.env', 'PROMETHEUS_PORT', 9090, 1, 65535)
    wanted = AGENT if mode == 'agent' else HYBRID
    writer = scrape(port, wanted)
    metrics = writer['metrics']
    report = {'mode': mode, 'observed_at': int(time.time()), 'writer': writer,
              'exporter': scrape(9179, EXPORTER)}
    if mode == 'agent':
        limit = config_number(directory/'remote.env', 'REMOTE_DISK_QUEUE_LIMIT', 2147483648, 512*1024**2, 1024**4)
        pending = metrics['vmagent_remotewrite_pending_data_bytes']
        report['configured_queue_limit_bytes'] = limit
        # This is a comparison against rendered config, not measured disk usage.
        report['pending_to_configured_limit_ratio'] = None if pending is None else pending / limit
    else:
        latest = metrics['prometheus_remote_storage_queue_highest_timestamp_seconds']
        sent = metrics['prometheus_remote_storage_queue_highest_sent_timestamp_seconds']
        report['estimated_send_lag_seconds'] = None if latest is None or sent is None or sent == 0 else max(0, latest-sent)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('mode', choices=['agent','hybrid'])
    args = p.parse_args()
    try:
        report = diagnose(args.directory,args.mode)
    except (OSError, ValueError):
        p.exit(2, 'Cannot read monitoring configuration.\n')
    print(json.dumps(report, indent=2, allow_nan=False))
    if not report['writer']['reachable'] or not report['exporter']['reachable'] or report['exporter']['metrics']['kvm_vm_collector_success'] != 1:
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
