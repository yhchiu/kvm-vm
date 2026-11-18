"""Opt-in two-tenant registration and queue handoff drill on a bootstrapped lab."""

import argparse
import json
import secrets
import shlex
import time
import urllib.parse
import uuid
from dataclasses import dataclass

import lab


@dataclass(frozen=True)
class Registration:
    name: str
    tenant: str
    host_id: str
    relay_port: int
    store_port: int

    def relabel(self):
        return [
            {"action": "labeldrop", "regex": "host_id|tenant_id|vm_account_id|vm_project_id"},
            {"target_label": "host_id", "replacement": self.host_id},
            {"target_label": "tenant_id", "replacement": self.tenant},
        ]


def ingress(registrations, password_file):
    routes = "\n".join(f"    {r.name} 127.0.0.1:{r.relay_port};" for r in registrations)
    return f"""map $remote_user $registration_upstream {{
    default "";
{routes}
}}
server {{
    listen 172.30.0.10:9443 ssl;
    ssl_certificate /etc/nginx/kvm-lab/server.pem;
    ssl_certificate_key /etc/nginx/kvm-lab/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    location = /api/v1/write {{
        limit_except POST {{ deny all; }}
        auth_basic "Lab registration";
        auth_basic_user_file {password_file};
        proxy_set_header Authorization "";
        proxy_pass_request_headers off;
        proxy_set_header Content-Type application/x-protobuf;
        proxy_set_header Content-Encoding snappy;
        proxy_pass http://$registration_upstream/api/v1/write;
    }}
    location / {{ return 404; }}
}}
"""


class Handoff:
    def __init__(self, controller):
        self.lab = controller
        nonce = uuid.uuid4().hex[:12]
        self.project = "registration-" + nonce
        self.home = "/root/" + self.project
        self.htpasswd = "/etc/nginx/kvm-lab/" + self.project + ".users"
        self.site = "/etc/nginx/sites-enabled/" + self.project + ".conf"
        host_id = controller.data["credentials"]["host-a"]["host_id"]
        self.a, self.b = [
            Registration(f"{self.project}-{key}", key, host_id, relay, store)
            for key, relay, store in [("a", 18429, 18428), ("b", 28429, 28428)]
        ]
        self.writer = self.project + "-writer"

    def put(self, node, filename, body, mode="0644"):
        self.lab.scp_bytes(node, body.encode(), filename, mode)

    def run(self, node, script, timeout=180):
        return self.lab.exec_guest(node, script, timeout=timeout)

    def central(self):
        # Separate Single backends and queues. Only nginx is reachable on the WAN.
        self.run("central", f"sudo install -m 0640 -o root -g www-data /dev/null {self.htpasswd}")
        self.put(
            "central",
            self.home + "/registrations.py",
            (lab.ROOT / "tools/monitoring/registrations.py").read_text(),
            "0600",
        )
        self.registry = f"sudo python3 {self.home}/registrations.py --file {self.htpasswd}"
        for r in (self.a, self.b):
            self.run(
                "central",
                f"{self.registry} stage {r.name} {r.host_id} {r.tenant} "
                f"{r.relay_port} {r.store_port}",
            )
        records = json.loads(self.run("central", self.registry + " list"))
        bindings = [
            Registration(**{k: v for k, v in row.items() if k != "state"}) for row in records
        ]
        for r in bindings:
            self.put("central", f"{self.home}/{r.name}.json", json.dumps(r.relabel()))
            self.run(
                "central",
                f"sudo docker run -d --name {r.name}-store --network host "
                f"-v {r.name}-store:/storage victoriametrics/victoria-metrics:v1.154.1 "
                f"-httpListenAddr=127.0.0.1:{r.store_port} -storageDataPath=/storage",
            )
            self.run(
                "central",
                f"sudo docker run -d --name {r.name}-relay --network host "
                f"-v {self.home}/{r.name}.json:/rules.json:ro -v {r.name}-relay:/queue "
                f"victoriametrics/vmagent:v1.154.1 -httpListenAddr=127.0.0.1:{r.relay_port} "
                f"-remoteWrite.relabelConfig=/rules.json -remoteWrite.tmpDataPath=/queue "
                f"-remoteWrite.url=http://127.0.0.1:{r.store_port}/api/v1/write",
            )
        self.put(
            "central",
            self.site,
            ingress(bindings, self.htpasswd) + self.private_reader_config(bindings),
        )
        self.run("central", "sudo nginx -t && sudo systemctl reload nginx")

    def private_reader_config(self, bindings):
        # Test principals may only query their own loopback store, from Central.
        users = self.htpasswd + ".readers"
        self.run("central", f"sudo install -m 0640 -o root -g www-data /dev/null {users}")
        self.put("central", self.home + "/ca.pem", (self.lab.home / "certs/ca.pem").read_text())
        mapping = []
        for r in bindings:
            reader = "reader-" + r.name
            password = secrets.token_urlsafe(24)
            path = self.home + "/reader-password"
            self.put("central", path, password + "\n", "0600")
            self.run("central", f"sudo sh -ec 'htpasswd -iB {users} {reader} < {path}; rm {path}'")
            self.put(
                "central",
                self.home + "/read-" + r.tenant,
                f'user = "{reader}:{password}"\n',
                "0600",
            )
            mapping.append(f"    {reader} 127.0.0.1:{r.store_port};")
        routes = "\n".join(mapping)
        return f"""map $remote_user $reader_store {{
    default "";
{routes}
}}
server {{
    listen 127.0.0.1:19445 ssl;
    ssl_certificate /etc/nginx/kvm-lab/server.pem;
    ssl_certificate_key /etc/nginx/kvm-lab/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    location = /api/v1/query {{
        limit_except GET {{ deny all; }}
        auth_basic "Lab private reader";
        auth_basic_user_file {users};
        proxy_pass_request_headers off;
        proxy_pass http://$reader_store/api/v1/query$is_args$args;
    }}
    location / {{ return 404; }}
}}
"""

    def read_as(self, r, generation, moment):
        params = urllib.parse.urlencode(
            {
                "query": f'count_over_time(up{{job="handoff",generation="{generation}"}}[30s])',
                "time": moment,
                "nocache": "1",
            }
        )
        url = shlex.quote("https://172.30.0.10:19445/api/v1/query?" + params)
        response = self.run(
            "central",
            f"sudo curl -fsS --config {self.home}/read-{r.tenant} "
            f"--cacert {self.home}/ca.pem --connect-to 172.30.0.10:19445:127.0.0.1:19445 {url}",
        )
        return json.loads(response)["data"]["result"]

    def credential(self, r, action, password=None):
        if action == "revoke":
            self.run("central", f"{self.registry} revoke {r.name}")
        else:
            path = self.home + "/new-password"
            self.put("central", path, password + "\n", "0600")
            self.run(
                "central", f"sudo sh -ec '{self.registry} {action} {r.name} < {path}; rm {path}'"
            )

    def probe(self, r, password):
        probe = self.home + "/probe"
        self.put("host-a", probe, f'user = "{r.name}:{password}"\n', "0600")
        return self.run(
            "host-a",
            f"sudo curl --config {probe} --cacert /etc/kvm-vm/monitoring/lab-ca.pem "
            '-sS -o /dev/null -w "%{http_code}" --max-time 10 '
            "-X POST https://172.30.0.10:9443/api/v1/write",
        )

    def start(self, r, password, scrape=True):
        cfg = {"global": {"scrape_interval": "2s"}, "scrape_configs": []}
        if scrape:
            cfg["scrape_configs"].append(
                {
                    "job_name": "handoff",
                    "static_configs": [
                        {
                            "targets": ["127.0.0.1:18439"],
                            "labels": {
                                "vm": "e2e-web01",
                                "generation": r.tenant,
                                "host_id": "sender",
                                "tenant_id": "sender",
                            },
                        }
                    ],
                    "metric_relabel_configs": [
                        {
                            "action": "keep",
                            "source_labels": ["__name__"],
                            "regex": "vm_app_start_timestamp",
                        }
                    ],
                }
            )
        self.put("host-a", self.home + "/scrape.json", json.dumps(cfg))
        self.put("host-a", self.home + "/password", password + "\n")
        self.run(
            "host-a",
            f"sudo docker run -d --name {self.writer} --network host "
            f"-v {r.name}-outbox:/queue -v {self.home}/scrape.json:/scrape.json:ro "
            f"-v {self.home}/password:/password:ro -v /etc/kvm-vm/monitoring/lab-ca.pem:/ca.pem:ro "
            "victoriametrics/vmagent:v1.154.1 -httpListenAddr=127.0.0.1:18439 "
            "-promscrape.config=/scrape.json -remoteWrite.tmpDataPath=/queue "
            "-remoteWrite.url=https://172.30.0.10:9443/api/v1/write "
            f"-remoteWrite.basicAuth.username={r.name} -remoteWrite.basicAuth.passwordFile=/password "
            "-remoteWrite.tlsCAFile=/ca.pem -remoteWrite.forcePromProto",
        )

    def stop(self):
        # SIGTERM flushes the in-memory tail to this registration's persistent queue.
        self.run("host-a", f"sudo docker stop -t 40 {self.writer} && sudo docker rm {self.writer}")

    def queue(self):
        lines = self.run(
            "host-a",
            "curl -fsS http://127.0.0.1:18439/metrics | "
            "grep '^vmagent_remotewrite_pending_data_bytes'",
        )
        return sum(float(s.split()[-1]) for s in lines.splitlines())

    def samples(self, r, start, end=None):
        end = end or int(time.time())
        text = self.run(
            "central",
            f"curl -fsSG --data-urlencode 'match[]=up{{job=\"handoff\"}}' "
            f"--data-urlencode start={start} --data-urlencode end={end} "
            f"http://127.0.0.1:{r.store_port}/api/v1/export",
        )
        values = {}
        for line in text.splitlines():
            row = json.loads(line)
            labels = row["metric"]
            if (labels["host_id"], labels["tenant_id"], labels["generation"]) != (
                r.host_id,
                r.tenant,
                r.tenant,
            ):
                raise RuntimeError(
                    "Cross-registration samples or untrusted identity in tenant store"
                )
            for ts, value in zip(row["timestamps"], row["values"]):
                if start * 1000 <= ts <= end * 1000:
                    values[ts] = value
        return values

    def exercise(self):
        base = int(time.time()) - 1
        config = "sudo /opt/kvm-vm/tools/setup-monitoring.sh"
        rule = "FORWARD -d 172.30.0.10 -p tcp --dport 9443 -j REJECT"
        pw_a, pw_rotated, pw_b = (secrets.token_urlsafe(24) for _ in range(3))
        self.credential(self.a, "activate", pw_a)
        self.run("host-a", config + " down --mode agent")
        try:
            self.start(self.a, pw_a)
            self.lab.until(lambda: self.samples(self.a, base), 90, "initial A sample")
            self.stop()
            self.credential(self.a, "rotate", pw_rotated)
            if self.probe(self.a, pw_a) != "401":
                raise RuntimeError("Retired credential was accepted")
            self.start(self.a, pw_rotated)
            rotated = int(time.time()) + 1
            self.lab.until(lambda: self.samples(self.a, rotated), 90, "rotated credential")
            self.run("router-a", "sudo iptables -I " + rule.replace("FORWARD ", "FORWARD 1 ", 1))
            start = int(time.time()) + 1
            try:
                self.lab.until(lambda: self.queue() > 0, 120, "old registration backlog")
                time.sleep(6)
                end = int(time.time()) - 1
                backlog = self.queue()
                self.stop()
                if self.samples(self.a, start, end):
                    raise RuntimeError("Old outage window already at backend")
                # Freeze collection; replay only A's outbox with A's credential.
                self.start(self.a, pw_rotated, scrape=False)
            finally:
                self.run("router-a", "sudo iptables -D " + rule)
            self.lab.until(
                lambda: self.queue() == 0 and self.samples(self.a, start, end),
                120,
                "durable replay into A",
            )
            self.stop()
            old = self.samples(self.a, base)
            self.credential(self.a, "revoke")
            if self.probe(self.a, pw_rotated) != "401":
                raise RuntimeError("Revoked registration was accepted")
            if self.samples(self.b, base):
                raise RuntimeError("B contains data before activation")
            self.credential(self.b, "activate", pw_b)
            self.start(self.b, pw_b)
            self.lab.until(lambda: self.samples(self.b, base), 90, "new B samples")
            if self.samples(self.a, base) != old:
                raise RuntimeError("Retired tenant history changed")
            new = self.samples(self.b, base)
            # First prove that both positive controls are queryable at their fixed
            # historical times. Raw export can see samples before instant queries.
            moments = {}
            for registration, history in ((self.a, old), (self.b, new)):
                moment = min(ts for ts, value in history.items() if value == 1) / 1000 + 1
                moments[registration.tenant] = moment
                self.lab.until(
                    lambda: bool(self.read_as(registration, registration.tenant, moment)),
                    120,
                    f"private reader {registration.tenant} sees its own samples",
                )
            for reader, other in ((self.a, self.b), (self.b, self.a)):
                if self.read_as(reader, other.tenant, moments[other.tenant]):
                    raise RuntimeError(
                        f"Reader {reader.tenant} returned samples belonging to {other.tenant}"
                    )
            return {
                "scoped_readers_verified": 2,
                "host_id": self.a.host_id,
                "outage_seconds": [start, end],
                "queue_bytes": backlog,
                "replayed_A_samples": len(self.samples(self.a, start, end)),
                "B_samples": len(self.samples(self.b, base)),
                "rotation_old_http": 401,
                "revoked_A_http": 401,
                "A_history_unchanged": True,
            }
        finally:
            self.run("host-a", f"sudo docker rm -f {self.writer} >/dev/null 2>&1 || true")
            self.run("host-a", config + " up --mode agent", timeout=480)

    def cleanup(self):
        self.run("central", f"sudo rm -f {self.site}; sudo nginx -t && sudo systemctl reload nginx")
        for r in (self.a, self.b):
            for suffix in ("relay", "store"):
                name = r.name + "-" + suffix
                self.run(
                    "central",
                    f"sudo docker rm -f {name} >/dev/null 2>&1 || true\n"
                    f"sudo docker volume rm {name} >/dev/null 2>&1 || true",
                )
            self.run("host-a", f"sudo docker volume rm {r.name}-outbox >/dev/null 2>&1 || true")
        self.run(
            "central",
            f"sudo rm -rf {self.home}; sudo rm -f {self.htpasswd} {self.htpasswd}.readers",
        )
        self.run("host-a", f"sudo rm -rf {self.home}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab-id", required=True)
    args = parser.parse_args()
    controller = lab.owned_lab(args.lab_id)
    exp = Handoff(controller)
    for node in ("central", "host-a"):
        exp.run(node, f"sudo install -d -m 0700 {exp.home}")
    try:
        exp.central()
        evidence = exp.exercise()
    finally:
        exp.cleanup()
    print(json.dumps({"status": "PASS", "finished": lab.now(), **evidence}, indent=2))


if __name__ == "__main__":
    main()
