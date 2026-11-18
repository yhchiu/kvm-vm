"""Interrupt the real installer after Compose starts the Agent, then retry it."""

import argparse
import json
import secrets

import lab


def exercise(t):
    host = "host-b"
    config = "/opt/kvm-vm/tools/setup-monitoring.sh"
    directory = "/root/writer-retry-" + secrets.token_hex(6)
    shell = lambda cmd: t.exec_guest(host, cmd, timeout=480)
    uid = json.loads(shell("sudo cat /etc/kvm-vm/monitoring/remote.json"))["host_id"]
    shell(f"sudo install -d -m0700 {directory}")
    t.scp_bytes(
        host,
        b"""#!/bin/bash
set -eu
/usr/bin/docker "$@"
if [[ "$1" == compose && "${*: -2}" == "up -d" ]]; then
    [[ $(ps -p "$PPID" -o args=) == *"/opt/kvm-vm/tools/setup-remote-monitoring.sh agent up"* ]]
    kill -KILL "$PPID"
fi
""",
        directory + "/docker",
        "0755",
    )
    shell(f"sudo {config} down --mode hybrid")
    try:
        failure = t.ssh(
            host,
            f"sudo env PATH={directory}:/usr/local/sbin:/usr/sbin:/usr/bin:/bin "
            f"{config} up --mode agent",
            check=False,
            timeout=480,
        )
        if failure.returncode != 137:
            raise lab.LabError(f"Expected interrupted installer, exit={failure.returncode}")
        if shell("sudo cat /etc/kvm-vm/monitoring/.mode.pending") != "agent":
            raise lab.LabError("Missing interrupted deployment intent")
        conflict = t.ssh(host, f"sudo {config} up --mode hybrid", check=False)
        if conflict.returncode == 0:
            raise lab.LabError("Conflicting deployment accepted")
        shell(f"sudo {config} up --mode agent")
        t.wait_for_writer(host, "agent", uid)
    finally:
        mode = shell(
            "sudo cat /etc/kvm-vm/monitoring/.mode.pending 2>/dev/null || "
            "sudo cat /etc/kvm-vm/monitoring/.mode 2>/dev/null || true"
        )
        if mode == "agent":
            shell(f"sudo {config} down --mode agent")
        shell(f"sudo {config} up --mode hybrid")
        shell(f"sudo rm -rf {directory}")
    t.wait_for_writer(host, "hybrid", uid)
    if json.loads(shell("sudo cat /etc/kvm-vm/monitoring/remote.json"))["host_id"] != uid:
        raise lab.LabError("Host ID changed after interrupted deployment")
    return {
        "status": "PASS",
        "utc": lab.now(),
        "host_id": uid,
        "interrupted_exit": failure.returncode,
        "conflict_exit": conflict.returncode,
        "fresh_samples": ["agent", "hybrid"],
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--lab-id", required=True)
    print(json.dumps(exercise(lab.owned_lab(p.parse_args().lab_id)), indent=2))
