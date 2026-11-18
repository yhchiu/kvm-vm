#!/usr/bin/env python3
"""Probe Central from an independent host and notify an HTTPS webhook on transitions."""
import argparse
import json
import os
from pathlib import Path
import ssl
import time
import uuid
import fcntl
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def https(value):
    url = urllib.parse.urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError('Expected HTTPS URL without userinfo or fragment')
    return value


def check_once(opener, health_file, webhook_file, state_file, threshold, failures):
    state = json.loads(state_file.read_text()) if state_file.exists() else {'healthy': True, 'pending': []}
    def save():
        temp = state_file.with_suffix('.tmp')
        with temp.open('w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, state_file)

    # Re-read the service endpoint at each probe to support a verified cutover.
    url = https(health_file.read_text().strip())
    try:
        with opener.open(url, timeout=5) as response:
            healthy = response.status == 200 and response.read(32).strip() == b'OK'
    except OSError:
        healthy = False
    failures = 0 if healthy else failures + 1
    if healthy != state['healthy'] and (healthy or failures >= threshold):
        status = 'resolved' if healthy else 'firing'
        state['healthy'] = healthy
        state['pending'].append({'id': uuid.uuid4().hex, 'status': status, 'time': time.time()})
        save()
    while state['pending']:
        event = state['pending'][0]
        status = event['status']
        payload = {**event, 'alerts': [{'status': status, 'labels': {'alertname': 'CentralUnavailable'}}]}
        request = urllib.request.Request(https(webhook_file.read_text().strip()), json.dumps(payload).encode(),
                                         {'Content-Type': 'application/json', 'Idempotency-Key': event['id']})
        try:
            with opener.open(request, timeout=5) as reply:
                if not 200 <= reply.status < 300:
                    raise OSError('Unsuccessful webhook response')
        except OSError:
            print('Notification failed; retrying on the next probe', flush=True)
            break
        state['pending'].pop(0)
        save()
        print(f'CentralUnavailable {status}', flush=True)
    return failures


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('health-file', 'webhook-file', 'state'):
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--ca-file')
    p.add_argument('--interval', type=int, default=15)
    p.add_argument('--failures', type=int, default=3)
    args = p.parse_args()
    if args.interval < 1 or args.failures < 1:
        p.error('interval and failures must be positive')
    os.umask(0o077)
    opener = urllib.request.build_opener(NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=args.ca_file)))
    lock = args.state.with_suffix('.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    failures = 0
    while True:
        failures = check_once(opener,args.health_file,args.webhook_file,args.state,args.failures,failures)
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
