#!/usr/bin/env python3
"""Run acceptance on an explicitly provisioned, disposable tenant fixture."""
import argparse
import json
import math
from pathlib import Path
import sys

from contract import Contract
from http_adapter import HTTPAdapter, private_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--allow-disposable-fixture', action='store_true', required=True)
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--quiet', type=float, default=5)
    p.add_argument('--report', type=Path, required=True)
    args = p.parse_args()
    if not all(math.isfinite(n) and 0 < n <= 120 for n in (args.timeout,args.quiet)):
        p.error('timeout and quiet must be positive and <=120s')
    # Reserve the output before doing any destructive fixture operation.
    with args.report.open('x',encoding='utf-8') as stream:
        try:
            adapter = HTTPAdapter(private_json(args.fixture))
            report = Contract(adapter, timeout=args.timeout, quiet=args.quiet).run()
        except Exception as exc:
            report = {'schema':1,'status':'BLOCKED','results':[{'scenario':'configuration','status':'BLOCKED','reason':type(exc).__name__}]}
        json.dump(report,stream,indent=2)
        stream.write('\n')
    print(report['status'])
    return {'PASS':0,'FAIL':1,'BLOCKED':2}[report['status']]


if __name__ == '__main__':
    sys.exit(main())
