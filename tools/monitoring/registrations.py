#!/usr/bin/env python3
"""Manage immutable writer registrations for the isolated tenant development ingress.

The administrator creates an empty root-owned, nginx-readable htpasswd file first.
Comments hold binding metadata. NGINX and the operator read the same atomic snapshot.
Production Central does not load this registry.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid


def read(path):
    records, hashes = {}, {}
    for line in path.read_text().splitlines():
        if line.startswith("# "):
            record = json.loads(line[2:])
            name = record["name"]
            if name in records:
                raise ValueError("Duplicate registration")
            records[name] = record
        elif line:
            name, verifier = line.split(":", 1)
            hashes[name] = verifier
    if {n for n, r in records.items() if r["state"] == "active"} != set(hashes):
        raise ValueError("Inconsistent registration snapshot")
    return records, hashes


def stage(records, name, host_id, tenant, relay_port, store_port):
    if not re.fullmatch("[a-z][a-z0-9-]{0,62}", name) or not re.fullmatch(
        "[a-z][a-z0-9-]{0,62}", tenant
    ):
        raise ValueError("Invalid registration or tenant name")
    if str(uuid.UUID(host_id)) != host_id or not all(
        1024 <= p <= 65535 for p in (relay_port, store_port)
    ):
        raise ValueError("Invalid host UUID or loopback port")
    if name in records or relay_port == store_port:
        raise ValueError("Registration already exists or ports conflict")
    for r in records.values():
        if relay_port in (r["relay_port"], r["store_port"]) or store_port == r["relay_port"]:
            raise ValueError("Registration relay must have a unique destination")
        if (tenant == r["tenant"]) != (store_port == r["store_port"]):
            raise ValueError("Each tenant must have its own store")
    records[name] = dict(
        name=name,
        host_id=host_id,
        tenant=tenant,
        relay_port=relay_port,
        store_port=store_port,
        state="staged",
    )


def change(records, hashes, command, name, make_verifier):
    record = records[name]
    if command == "revoke" and record["state"] == "revoked":
        return
    if command == "activate":
        if record["state"] != "staged" or any(
            r["host_id"] == record["host_id"] and r["state"] == "active" for r in records.values()
        ):
            raise ValueError("Activation requires a staged registration and a released host")
    elif record["state"] != "active":
        raise ValueError("Registration is not active")
    if command == "revoke":
        del hashes[name]
        record["state"] = "revoked"
    else:
        hashes[name] = make_verifier(name)
        record["state"] = "active"


def verifier(name):
    password = sys.stdin.buffer.readline(4097).rstrip(b"\r\n")
    if not 1 <= len(password) <= 72 or b"\x00" in password or sys.stdin.buffer.read(1):
        raise ValueError("Provide one password line (1 to 72 bytes) on stdin")
    result = subprocess.run(
        ["htpasswd", "-niB", name], input=password + b"\n", capture_output=True, check=True
    )
    username, digest = result.stdout.decode().strip().split(":", 1)
    if username != name or not re.fullmatch(r"\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}", digest):
        raise ValueError("Invalid bcrypt verifier")
    return digest


def write(path, records, hashes):
    info = path.stat()
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            os.fchown(f.fileno(), info.st_uid, info.st_gid)
            os.fchmod(f.fileno(), stat.S_IMODE(info.st_mode))
            for name in sorted(records):
                print("# " + json.dumps(records[name], sort_keys=True), file=f)
                if name in hashes:
                    print(f"{name}:{hashes[name]}", file=f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--file", type=Path, required=True)
    sub = p.add_subparsers(dest="command", required=True)
    enroll = sub.add_parser("stage")
    for field in ("name", "host_id", "tenant"):
        enroll.add_argument(field)
    for field in ("relay_port", "store_port"):
        enroll.add_argument(field, type=int)
    for command in ("activate", "rotate", "revoke"):
        sub.add_parser(command).add_argument("name")
    sub.add_parser("list")
    args = p.parse_args()
    path = args.file.absolute()
    if path != path.resolve() or not path.is_file():
        p.error("Provide an existing regular registry file without symbolic links")
    lock = os.open(path.parent, os.O_RDONLY)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records, hashes = read(path)
        if args.command == "list":
            print(json.dumps(list(records.values())))
            return
        if args.command == "stage":
            stage(records, args.name, args.host_id, args.tenant, args.relay_port, args.store_port)
        else:
            change(records, hashes, args.command, args.name, verifier)
        write(path, records, hashes)
        os.fsync(lock)
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        p.exit(1, f"Registration operation failed: {exc}\n")
    finally:
        os.close(lock)


if __name__ == "__main__":
    main()
