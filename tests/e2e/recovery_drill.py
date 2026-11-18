"""Cold restore into a separate blank VM with a live external observer and TLS receiver."""

import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile
import time

import lab

HERE = Path(__file__).resolve().parent
SETUP = "/opt/kvm-vm/tools/setup-central-monitoring.sh"


def copy_guest_file(t, source, dest, path):
    # The backup stream stays inside the lab controller and encrypted SSH sessions.
    def argv(node, command):
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={t.home / 'known_hosts'}",
            "-i",
            str(t.home / "id_ed25519"),
            "lab@" + t.nodes[node]["wan"],
            command,
        ]

    with subprocess.Popen(
        argv(source, "sudo cat " + shlex.quote(path)), stdout=subprocess.PIPE
    ) as read:
        try:
            command = "umask 077; cat > " + shlex.quote(path)
            subprocess.run(
                argv(dest, "sudo sh -c " + shlex.quote(command)),
                stdin=read.stdout,
                check=True,
                timeout=600,
            )
        finally:
            read.stdout.close()
        if read.wait(timeout=30):
            raise lab.LabError("Backup transfer failed on source")


class Drill:
    def __init__(self, controller, ca_dir):
        self.ca_dir = ca_dir
        self.t = controller
        self.work = "/root/dr-" + secrets.token_hex(6)
        self.unit = Path(self.work).name
        self.bundle = self.work + "/bundle"

    def run(self, node, command, timeout=180):
        return self.t.exec_guest(node, command, timeout=timeout)

    def put(self, node, path, data):
        self.t.scp_bytes(node, data.encode() if isinstance(data, str) else data, path, "0600")

    def tls(self, node, ip):
        with tempfile.TemporaryDirectory(dir=self.t.home) as tmp:
            pem = Path(tmp)
            key, csr, cert, ext = [pem / s for s in ("server.key", "request", "server.pem", "ext")]
            ext.write_text(
                f"subjectAltName=IP:{ip}\nextendedKeyUsage=serverAuth\nbasicConstraints=CA:FALSE\n"
            )
            lab.run(
                [
                    "openssl",
                    "req",
                    "-new",
                    "-nodes",
                    "-newkey",
                    "rsa:2048",
                    "-subj",
                    "/CN=lab-dr",
                    "-keyout",
                    key,
                    "-out",
                    csr,
                ]
            )
            lab.run(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-sha256",
                    "-days",
                    "2",
                    "-in",
                    csr,
                    "-CA",
                    self.ca_dir / "ca.pem",
                    "-CAkey",
                    self.ca_dir / "ca.key",
                    "-set_serial",
                    str(secrets.randbits(120) + 1),
                    "-extfile",
                    ext,
                    "-out",
                    cert,
                ]
            )
            self.put(node, self.work + "/key", key.read_bytes())
            self.put(node, self.work + "/cert", cert.read_bytes())

    def health_endpoint(self, node):
        ip = self.t.nodes[node]["wan"]
        self.tls(node, ip)
        self.put(
            node,
            "/etc/nginx/sites-enabled/" + self.unit + ".conf",
            f"""server {{
    listen {ip}:9444 ssl;
    ssl_certificate {self.work}/cert;
    ssl_certificate_key {self.work}/key;
    location = /health {{
        allow 172.30.0.12;
        deny all;
        proxy_pass http://127.0.0.1:8428/health;
    }}
    location / {{ return 404; }}
}}
""",
        )
        self.run(node, "sudo nginx -t && sudo systemctl reload nginx")

    def observer(self):
        self.tls("router-a", "172.30.0.11")
        token = secrets.token_urlsafe(24)
        self.put("router-a", self.work + "/path", token)
        self.put("router-a", self.work + "/receive.py", (HERE / "dr_receiver.py").read_bytes())
        self.put(
            "router-b",
            self.work + "/watch.py",
            (lab.ROOT / "tools/monitoring/watch-central.py").read_bytes(),
        )
        self.put("router-b", self.work + "/webhook", "https://172.30.0.11:19443/" + token)
        self.put("router-b", self.work + "/ca", (self.ca_dir / "ca.pem").read_bytes())
        self.point_to("central")
        self.run(
            "router-a",
            f"sudo systemd-run --unit={self.unit}-receiver --working-directory={self.work} "
            f"/usr/bin/python3 {self.work}/receive.py",
        )
        # Verify TLS and receiver authentication from the observer before the incident.
        probe = f"""sudo python3 - <<'END'
import pathlib, ssl, urllib.request
p = pathlib.Path("{self.work}")
ctx = ssl.create_default_context(cafile=p / "ca")
with urllib.request.urlopen((p / "webhook").read_text().strip(), context=ctx, timeout=5) as reply:
    assert reply.status == 204
END"""
        self.t.until(lambda: self.run("router-b", probe) == "", 20, "receiver preflight", pause=2)
        self.run(
            "router-b",
            f"sudo systemd-run --unit={self.unit}-observer --working-directory={self.work} "
            f"/usr/bin/python3 {self.work}/watch.py --health-file health --webhook-file webhook "
            "--ca-file ca --state state.json --interval 2 --failures 2",
        )
        # A verified 200 before the incident rules out a permanently failed probe.
        self.run(
            "router-b", f"sudo curl --cacert {self.work}/ca -fsS https://172.30.0.10:9444/health"
        )

    def point_to(self, node):
        ip = self.t.nodes[node]["wan"]
        self.put("router-b", self.work + "/health.new", f"https://{ip}:9444/health")
        self.run("router-b", f"sudo mv {self.work}/health.new {self.work}/health")

    def receipts(self):
        text = self.run("router-a", f"sudo cat {self.work}/receipts 2>/dev/null || true")
        return [json.loads(line) for line in text.splitlines()]

    def data(self, node, start, end):
        selector = shlex.quote("match[]=kvm_vm_collector_success")
        rows = self.run(
            node,
            f"curl -fsSG --data-urlencode {selector} --data-urlencode start={start} "
            f"--data-urlencode end={end} http://127.0.0.1:8428/api/v1/export",
        )
        values = sorted(
            (json.dumps(r["metric"], sort_keys=True), ts, v)
            for row in rows.splitlines()
            for r in [json.loads(row)]
            for ts, v in zip(r["timestamps"], r["values"])
            if start * 1000 <= ts <= end * 1000
        )
        return values

    def grafana(self, node):
        # Keep the administrator credential inside the guest. Print only dashboard UIDs.
        cmd = """sudo python3 - <<'END'
import base64,json,urllib.request
cfg=dict(l.strip().split('=',1) for l in open('/etc/kvm-vm/central/.env') if '=' in l)
auth=base64.b64encode((cfg['GRAFANA_ADMIN_USER']+':'+cfg['GRAFANA_ADMIN_PASSWORD']).encode()).decode()
req=urllib.request.Request('http://127.0.0.1:'+cfg['GRAFANA_PORT']+'/api/search?type=dash-db',headers={'Authorization':'Basic '+auth})
print(json.dumps(sorted(r['uid'] for r in json.load(urllib.request.urlopen(req,timeout=6)))))
END"""
        return json.loads(self.run(node, cmd))

    def exercise(self):
        t = self.t
        with contextlib.redirect_stdout(sys.stderr):
            t.prepare_restore_vm()
        for n in ("central", "restore", "router-a", "router-b"):
            self.run(n, f"sudo install -d -m0700 {self.work}")
        self.run("restore", "test ! -e /etc/kvm-vm/central")
        t.docker_packages("restore", ["nginx", "curl"])
        self.run("restore", "sudo systemctl enable --now nginx")
        t.install_repo("restore")
        for n in ("central", "restore"):
            self.put(n, self.work + "/cold.sh", (HERE / "cold_backup.sh").read_bytes())
            self.health_endpoint(n)
        backup = f"sudo bash {self.work}/cold.sh"
        end = int(time.time()) - 12
        start = end - 90
        samples = self.data("central", start, end)
        if not samples:
            raise lab.LabError("Need live samples before restore exercise")
        inventory = json.loads(self.run("central", "sudo " + SETUP + " inventory list"))
        dashboards = self.grafana("central")
        if len(inventory) != 2 or not dashboards:
            raise lab.LabError("Central inventory or dashboards incomplete")
        self.run("central", f"{backup} backup {self.bundle}", timeout=600)
        # Verify checksums independently on the blank guest before starting restore.
        self.run("restore", f"sudo install -d -m0700 {self.bundle}")
        for name in ("config.tar", "volume-0.tar", "volume-1.tar", "SHA256SUMS"):
            copy_guest_file(t, "central", "restore", self.bundle + "/" + name)
        self.observer()
        # Suspend only the exact manifest-owned source VM; the receiver is a separate VM.
        domain = lab.domain_name(t.lab_id, "central")
        t.assert_owned_domain(domain)
        incident = time.time()
        t.virsh("suspend", domain)
        try:
            t.until(
                lambda: any(r["status"] == "firing" for r in self.receipts()),
                90,
                "external firing receipt",
                pause=2,
            )
            self.run("restore", f"{backup} restore {self.bundle}", timeout=900)
            t.until(
                lambda: self.data("restore", start, end) == samples, 120, "restored raw samples"
            )
            if json.loads(self.run("restore", "sudo " + SETUP + " inventory list")) != inventory:
                raise lab.LabError("Inventory differs after restore")
            t.until(
                lambda: self.grafana("restore") == dashboards,
                300,
                "restored Grafana dashboard inventory",
            )
            self.point_to("restore")
            t.until(
                lambda: any(r["status"] == "resolved" for r in self.receipts()),
                90,
                "external recovery receipt",
                pause=2,
            )
            retrieved = time.time()
            last = self.data("restore", int(incident) - 600, int(incident))
            if not last:
                raise lab.LabError("No recent restored collector samples")
            per_host = {}
            for labels, ts, _ in last:
                host = json.loads(labels)["host_id"]
                per_host[host] = max(ts, per_host.get(host, 0))
            if set(per_host) != {r["host_id"] for r in inventory}:
                raise lab.LabError("Missing restored host")
            return {
                "status": "PASS",
                "utc": lab.now(),
                "recovery_seconds": round(retrieved - incident, 1),
                "seconds_since_last_sample_by_host": {
                    k: round(incident - v / 1000, 1) for k, v in per_host.items()
                },
                "fixed_window": [start, end],
                "verified_samples": len(samples),
                "inventory_count": len(inventory),
                "dashboard_uids": dashboards,
                "receipts": self.receipts(),
                "sample_digest": hashlib.sha256(json.dumps(samples).encode()).hexdigest(),
            }
        finally:
            t.virsh("resume", domain)
            self.align_source_clock()

    def align_source_clock(self):
        # KVM can retain a synchronized status while its clock is a full suspend
        # interval behind. Subsequent metric and alert queries depend on UTC.
        def skew():
            guest = int(self.run("central", "date +%s"))
            return int(time.time()) - guest

        before = skew()
        if abs(before) <= 3:
            return before, before
        self.run("central", f"sudo date -u --set=@{int(time.time())} >/dev/null")
        after = skew()
        if abs(after) > 3:
            raise lab.LabError("Resumed Central clock differs from lab controller")
        return before, after

    def cleanup(self):
        for node, role in [("router-b", "observer"), ("router-a", "receiver")]:
            self.run(node, f"sudo systemctl stop {self.unit}-{role} 2>/dev/null || true")
        for node in ("central", "restore"):
            self.run(
                node,
                f"sudo rm -f /etc/nginx/sites-enabled/{self.unit}.conf\n"
                "if command -v nginx >/dev/null; then sudo nginx -t && sudo systemctl reload nginx; fi",
            )
        for node in ("central", "restore", "router-a", "router-b"):
            self.run(node, f"sudo rm -rf {self.work}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-id", required=True)
    t = lab.owned_lab(parser.parse_args().lab_id)
    with tempfile.TemporaryDirectory(dir=t.home) as ca_dir:
        ca = Path(ca_dir)
        lab.generate_ca(ca)
        drill = Drill(t, ca)
        try:
            result = drill.exercise()
        finally:
            if t.data.get("restore_vm"):
                drill.cleanup()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
