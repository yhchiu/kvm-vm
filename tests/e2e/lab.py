#!/usr/bin/env python3
"""Opt-in, isolated libvirt central-monitoring E2E lab. No third-party Python dependencies."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PREFIX = 'kvm-e2e-'
WAN = '172.30.0'
HOSTS = ('host-a', 'host-b')
# Private lab WAN and two independently isolated LANs. The physical hypervisor
# is a privileged management controller and can reach all three bridge IPs.
NODES = {
    'central': {'wan': '172.30.0.10', 'ram': 4096, 'vcpus': 2, 'disk': 24},
    'router-a': {'wan': '172.30.0.11', 'lan': '10.10.1.2', 'ram': 768, 'vcpus': 1, 'disk': 8},
    'router-b': {'wan': '172.30.0.12', 'lan': '10.10.2.2', 'ram': 768, 'vcpus': 1, 'disk': 8},
    'host-a': {'lan': '10.10.1.10', 'ram': 6144, 'vcpus': 2, 'disk': 36},
    'host-b': {'lan': '10.10.2.10', 'ram': 6144, 'vcpus': 2, 'disk': 36},
}
SCENARIOS = ('connectivity', 'double-nat', 'metrics', 'same-name-vms',
             'latency-loss', 'disconnect-recover', 'auth', 'host-offline', 'mode-switch')


class LabError(Exception):
    pass


def run(args, *, input=None, check=True, timeout=180, text=True):
    proc = subprocess.run([str(x) for x in args], input=input, text=text,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout, check=False)
    if check and proc.returncode != 0:
        error = proc.stderr.strip() if text else 'nonzero exit'
        raise LabError(f"{shlex.join([str(x) for x in args[:4]])}: {error[-500:]}")
    return proc


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def mac_for(node, index, lab_id):
    # Stable locally-administered unicast MACs; lab IDs are hashed to avoid
    # collisions for multiple independently named labs on the same machine.
    import hashlib
    digest = hashlib.sha256(f'{lab_id}/{node}/{index}'.encode()).digest()
    return '52:54:00:%02x:%02x:%02x' % tuple(digest[:3])


def net_xml(name, address, mode):
    n = ET.Element('network')
    ET.SubElement(n, 'name').text = name
    if mode == 'nat':
        ET.SubElement(n, 'forward', mode='nat')
    # Fixed bridge address keeps the controller capable of provisioning isolated nodes.
    ip = ET.SubElement(n, 'ip', address=address, netmask='255.255.255.0')
    return ET.tostring(n, encoding='unicode')


def cloud_network(node, lab_id):
    info = NODES[node]
    configs = []
    for idx, kind in enumerate(('wan', 'lan')):
        if kind not in info:
            continue
        ip = info[kind]
        gateway = ('172.30.0.1' if kind == 'wan' else
                   ('10.10.1.2' if node == 'host-a' else '10.10.2.2')
                   if node in HOSTS else None)
        lines = [f'    nic{idx}:', f'      match: {{macaddress: "{mac_for(node, kind, lab_id)}"}}',
                 f'      set-name: {kind}0', f'      addresses: [{ip}/24]',
                 '      dhcp4: false', '      dhcp6: false']
        if gateway:
            lines += [f'      routes: [{{to: default, via: {gateway}}}]',
                      '      nameservers: {addresses: [1.1.1.1, 9.9.9.9]}']
        configs.extend(lines)
    return 'version: 2\nethernets:\n' + '\n'.join(configs) + '\n'


def cloud_user_data(node, pubkey):
    if not pubkey.startswith(('ssh-ed25519 ', 'ssh-rsa ', 'ecdsa-sha2-')):
        raise LabError('SSH public key has unexpected format')
    return (f'#cloud-config\nhostname: {node}\nmanage_etc_hosts: true\n'
            'ssh_pwauth: false\ndisable_root: true\n'
            'users:\n  - name: lab\n    shell: /bin/bash\n'
            '    sudo: ALL=(ALL) NOPASSWD:ALL\n    groups: [sudo]\n'
            '    lock_passwd: true\n    ssh_authorized_keys:\n'
            f'      - {pubkey.strip()}\n')


def net_names(lab_id):
    return {n: f'{PREFIX}{lab_id}-{n}' for n in ('wan', 'site-a', 'site-b')}


def domain_name(lab_id, node):
    return f'{PREFIX}{lab_id}-{node}'


def topo(lab_id):
    nets = net_names(lab_id)
    return {
        'lab_id': lab_id, 'networks': {
            nets['wan']: {'mode': 'nat', 'cidr': '172.30.0.0/24', 'bridge': '172.30.0.1'},
            nets['site-a']: {'mode': 'isolated', 'cidr': '10.10.1.0/24', 'bridge': '10.10.1.1'},
            nets['site-b']: {'mode': 'isolated', 'cidr': '10.10.2.0/24', 'bridge': '10.10.2.1'},
        }, 'domains': {domain_name(lab_id, n): {'role': n, **v} for n, v in NODES.items()},
        'notes': ['WAN libvirt NAT + per-site router NAT = nested NAT to Internet',
                  'isolated site LANs; physical hypervisor bridges are out-of-band management',
                  'central has no configured route into private site LANs',
                  'no physical host firewall or libvirt default-network changes'],
    }


class Lab:
    def __init__(self, args):
        self.args = args
        self.lab_id = args.lab_id
        root = Path(os.environ.get('KVM_VM_E2E_STATE_ROOT', '/var/lib/kvm-vm-e2e-lab'))
        self.home = root / self.lab_id
        self.manifest = self.home / 'state.json'
        self.names = net_names(self.lab_id)
        self.uri = os.environ.get('KVM_VM_E2E_LIBVIRT_URI', 'qemu:///system')
        self.data = None

    @property
    def image_dir(self):
        if self.data and self.data.get('separate_images'):
            directory = self.home.with_name(self.lab_id + '.images')
            if directory.is_symlink():
                raise LabError('Refusing symlink in lab image directory')
            return directory
        return self.home

    def create_image_dir(self):
        # QEMU needs traversal. The manifest, credentials and keys stay in home.
        self.image_dir.mkdir(mode=0o711)
        self.image_dir.chmod(0o711)

    def read_state(self):
        if self.home.is_symlink() or self.manifest.is_symlink():
            raise LabError('Refusing symbolic link in lab state directory')
        if not self.manifest.is_file():
            raise LabError(f'Lab not initialized: {self.manifest}. Run up first.')
        s = json.loads(self.manifest.read_text())
        if s.get('lab_id') != self.lab_id or s.get('owner') != 'kvm-vm-e2e-lab-v1':
            raise LabError('Unrecognized state file, refusing operation')
        self.data = s
        return s

    def write_state(self):
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.home, 0o700)
        tmp = self.home / '.state.new'
        if tmp.is_symlink():
            raise LabError('State temp file is a symlink')
        tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True) + '\n')
        os.chmod(tmp, 0o600)
        tmp.replace(self.manifest)

    def virsh(self, *args, check=True):
        return run(['virsh', '-c', self.uri, *args], check=check)

    def is_defined(self, category, name):
        cmd = 'dominfo' if category == 'domain' else 'net-info'
        return self.virsh(cmd, name, check=False).returncode == 0

    def assert_owned_domain(self, name):
        xml = ET.fromstring(self.virsh('dumpxml', name).stdout)
        expected = str(self.image_dir / (name + '.qcow2'))
        files = [el.attrib.get('file') for el in xml.findall('./devices/disk/source')]
        if expected not in files:
            raise LabError(f'Refusing unexpected domain {name}: lab overlay disk not attached')

    def assert_owned_network(self, name, expected_ip, mode):
        xml = ET.fromstring(self.virsh('net-dumpxml', name).stdout)
        ip = xml.find('./ip')
        forward = xml.find('./forward')
        actual = forward.attrib.get('mode') if forward is not None else 'isolated'
        if ip is None or ip.attrib.get('address') != expected_ip or actual != mode:
            raise LabError(f'Refusing modified or foreign libvirt network: {name}')

    def preflight(self, image):
        if sys.platform != 'linux' or os.geteuid() != 0:
            raise LabError('Lab creation requires root on a disposable Linux KVM hypervisor')
        for bin in ('virsh', 'virt-install', 'qemu-img', 'cloud-localds', 'ssh-keygen',
                    'ssh', 'tar', 'ip'):
            if not shutil.which(bin):
                raise LabError(f'Missing {bin}; install libvirt, virtinst, qemu-utils, cloud-image-utils, openssh-client')
        if not Path('/dev/kvm').exists():
            raise LabError('/dev/kvm missing on physical host')
        if not image.is_file() or image.is_symlink():
            raise LabError('Provide an existing, trusted cloud-init QCOW2 with --image /absolute/path')
        if not image.is_absolute() or str(image).startswith(str(self.home)):
            raise LabError('Cloud image must be absolute and outside the disposable lab directory')
        self.virsh('uri')
        if not self.manifest.exists():
            # Avoid colliding with active physical LANs, VPNs or other labs.
            reserved = [ipaddress.ip_network(x) for x in
                        ('172.30.0.0/24', '10.10.1.0/24', '10.10.2.0/24')]
            routes = run(['ip', '-4', '-o', 'route', 'show']).stdout.splitlines()
            for line in routes:
                try:
                    raw = line.split()[0]
                    if raw == 'default':
                        continue
                    route = ipaddress.ip_network(raw, strict=False)
                except (ValueError, IndexError):
                    continue
                if any(route.overlaps(subnet) for subnet in reserved):
                    raise LabError(f'Lab subnet overlaps an existing host route: {route}')
            conflicts = [x for x in self.names.values() if self.is_defined('net', x)]
            conflicts += [domain_name(self.lab_id, n) for n in NODES
                          if self.is_defined('domain', domain_name(self.lab_id, n))]
            if conflicts:
                raise LabError('Refusing to adopt pre-existing libvirt resources: ' + ', '.join(conflicts))

    def up(self, image):
        self.preflight(image)
        if self.manifest.exists():
            self.read_state()
            if self.data['image'] != str(image.resolve()):
                raise LabError('Existing lab uses another base image; down before changing')
        else:
            if self.home.exists() and any(self.home.iterdir()):
                raise LabError('Existing nonempty state directory has no manifest; investigate before reusing')
            self.home.mkdir(mode=0o700, parents=True)
            os.chmod(self.home, 0o700)
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.home / 'id_ed25519')])
            self.data = {'owner': 'kvm-vm-e2e-lab-v1', 'lab_id': self.lab_id,
                         'image': str(image.resolve()), 'created': now(),
                         'credentials': {h: {'host_id': str(uuid.uuid4()),
                                             'password': secrets.token_urlsafe(24)} for h in HOSTS},
                         'bootstrapped': False, 'separate_images': True}
            self.create_image_dir()
            self.write_state()
        for name, cfg in topo(self.lab_id)['networks'].items():
            if self.is_defined('net', name):
                self.assert_owned_network(name, cfg['bridge'], cfg['mode'])
                info = self.virsh('net-info', name).stdout
                if re.search(r'^Active:\s+no$', info, flags=re.MULTILINE | re.IGNORECASE):
                    self.virsh('net-start', name)
                continue
            mode = cfg['mode']
            xml = net_xml(name, cfg['bridge'], mode)
            xfile = self.home / (name + '.xml')
            xfile.write_text(xml)
            self.virsh('net-define', str(xfile))
            self.virsh('net-start', name)
        pub = (self.home / 'id_ed25519.pub').read_text().strip()
        for node, info in NODES.items():
            name = domain_name(self.lab_id, node)
            if self.is_defined('domain', name):
                self.assert_owned_domain(name)
                continue
            disk = self.image_dir / f'{name}.qcow2'
            seed = self.image_dir / f'{name}-seed.img'
            if disk.exists() or seed.exists():
                # Manifest ownership was established before creation; a partial
                # virt-install failure may leave only lab-owned local files.
                disk.unlink(missing_ok=True)
                seed.unlink(missing_ok=True)
            run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(image.resolve()),
                 str(disk), f'{info["disk"]}G'])
            user = self.home / f'{node}-user-data'
            meta = self.home / f'{node}-meta-data'
            net = self.home / f'{node}-network-config'
            user.write_text(cloud_user_data(node, pub))
            meta.write_text(f'instance-id: {name}\nlocal-hostname: {node}\n')
            net.write_text(cloud_network(node, self.lab_id))
            run(['cloud-localds', '--network-config', str(net), str(seed), str(user), str(meta)])
            disk.chmod(0o600)
            seed.chmod(0o600)
            v = ['virt-install', '--connect', self.uri, '--name', name,
                 '--memory', str(info['ram']), '--vcpus', str(info['vcpus']),
                 '--cpu', 'host-passthrough', '--import', '--os-variant', 'generic',
                 '--disk', f'path={disk},format=qcow2,bus=virtio',
                 '--disk', f'path={seed},device=cdrom', '--graphics', 'none',
                 '--noautoconsole', '--wait', '0']
            for kind in ('wan', 'lan'):
                if kind not in info:
                    continue
                site = 'site-a' if node.endswith('-a') else 'site-b'
                network = self.names['wan'] if kind == 'wan' else self.names[site]
                v += ['--network', f'network={network},model=virtio,mac={mac_for(node, kind, self.lab_id)}']
            run(v, timeout=180)
        print('Lab VMs and isolated networks created. Next: lab.sh bootstrap')

    def ssh(self, node, script, *, input=None, timeout=120, check=True):
        if node not in NODES:
            raise LabError(f'Unknown node: {node}')
        if self.data is None:
            self.read_state()
        address = NODES[node].get('lan', NODES[node].get('wan'))
        return run(['ssh', '-i', str(self.home / 'id_ed25519'), '-o', 'BatchMode=yes',
                    '-o', 'StrictHostKeyChecking=accept-new', '-o',
                    f'UserKnownHostsFile={self.home / "known_hosts"}',
                    '-o', 'ConnectTimeout=5', f'lab@{address}', 'bash', '-se'],
                   input=script if input is None else input,
                   check=check, timeout=timeout)

    def scp_bytes(self, node, data, dest, mode='0644'):
        """Stream binary bytes through SSH stdin, not shell argv or temporary URLs."""
        if self.data is None:
            self.read_state()
        if node not in NODES or not dest.startswith('/'):
            raise LabError('Invalid lab upload destination')
        address = NODES[node].get('lan', NODES[node].get('wan'))
        remote = (f'sudo mkdir -p {shlex.quote(str(Path(dest).parent))} && '
                  f'sudo tee {shlex.quote(dest)} >/dev/null && '
                  f'sudo chmod {mode} {shlex.quote(dest)}')
        cmd = ['ssh', '-i', str(self.home / 'id_ed25519'), '-o', 'BatchMode=yes',
               '-o', 'StrictHostKeyChecking=accept-new', '-o',
               f'UserKnownHostsFile={self.home / "known_hosts"}',
               '-o', 'ConnectTimeout=5', f'lab@{address}', remote]
        run(cmd, input=data, text=False, timeout=180)

    def ssh_wait(self, node, timeout=240):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r = self.ssh(node, 'true\n', timeout=15, check=False)
            if r.returncode == 0:
                return
            time.sleep(5)
        raise LabError(f'{node} SSH not available; check cloud-init/guest console')

    def apt(self, node, packages):
        txt = ('sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq\n'
               'sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq '
               + ' '.join(packages) + '\n')
        self.ssh(node, txt, timeout=900)

    def docker_packages(self, node, extra):
        # Debian 13 and Ubuntu 24.04 have differently named Compose v2 packages.
        self.apt(node, ['docker.io', *extra])
        self.ssh(node, '''if ! docker compose version >/dev/null 2>&1; then
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 ||
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
fi
sudo systemctl enable --now docker
sudo docker compose version
''', timeout=480)

    def install_repo(self, node):
        # Archive only tracked-like project sources, never local secrets or tests
        # created in /mnt/data or a nested Git checkout.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode='w:gz') as t:
            for source in ROOT.rglob('*'):
                if not source.is_file() or '.git' in source.parts or '__pycache__' in source.parts:
                    continue
                if any(part in ('reports', '.pytest_cache', '.venv') for part in source.parts):
                    continue
                rel = source.relative_to(ROOT)
                t.add(source, arcname=str(Path('kvm-vm') / rel))
        self.scp_bytes(node, buf.getvalue(), '/tmp/kvm-e2e-project.tar.gz', '0600')
        self.ssh(node, 'sudo mkdir -p /opt/kvm-vm\n'
                 'sudo tar -xzf /tmp/kvm-e2e-project.tar.gz -C /opt\n'
                 'sudo chmod +x /opt/kvm-vm/install.sh /opt/kvm-vm/kvm-vm /opt/kvm-vm/tools/*.sh\n'
                 'sudo /opt/kvm-vm/install.sh\n', timeout=120)

    def bootstrap_router(self, node):
        idx = '1' if node == 'router-a' else '2'
        self.apt(node, ['iptables', 'iproute2'])
        script = f'''#!/bin/sh
set -eu
sysctl -w net.ipv4.ip_forward=1
iptables -P FORWARD DROP
iptables -F FORWARD
iptables -t nat -F POSTROUTING
iptables -A FORWARD -i lan0 -o wan0 -j ACCEPT
iptables -A FORWARD -i wan0 -o lan0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -t nat -A POSTROUTING -s 10.10.{idx}.0/24 -o wan0 -j MASQUERADE
'''
        self.scp_bytes(node, script.encode(), '/usr/local/sbin/kvm-lab-router-nat', '0755')
        service = '''[Unit]
Description=Isolated KVM E2E lab router NAT
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kvm-lab-router-nat
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
'''
        self.scp_bytes(node, service.encode(), '/etc/systemd/system/kvm-lab-router.service')
        self.ssh(node, 'sudo systemctl daemon-reload\n'
                 'sudo systemctl enable --now kvm-lab-router.service\n'
                 'ip -4 route\n')

    def generate_certs(self):
        certs = self.home / 'certs'
        certs.mkdir(mode=0o700, exist_ok=True)
        ca, ca_key = certs / 'ca.pem', certs / 'ca.key'
        cert, key = certs / 'server.pem', certs / 'server.key'
        if not ca.exists():
            run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes',
                 '-sha256', '-days', '7', '-subj', '/CN=KVM E2E Lab CA',
                 '-addext', 'basicConstraints=critical,CA:TRUE',
                 '-keyout', str(ca_key), '-out', str(ca)])
            run(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes',
                 '-subj', '/CN=kvm-e2e-central', '-keyout', str(key),
                 '-out', str(certs / 'server.csr')])
            ext = certs / 'server.ext'
            ext.write_text('subjectAltName=IP:172.30.0.10\n'
                           'basicConstraints=CA:FALSE\n'
                           'extendedKeyUsage=serverAuth\n')
            run(['openssl', 'x509', '-req', '-sha256', '-days', '7',
                 '-in', str(certs / 'server.csr'), '-CA', str(ca), '-CAkey', str(ca_key),
                 '-CAcreateserial', '-extfile', str(ext), '-out', str(cert)])
            os.chmod(ca_key, 0o600)
            os.chmod(key, 0o600)
        return ca.read_bytes(), cert.read_bytes(), key.read_bytes()

    def bootstrap_central(self):
        self.docker_packages('central', ['nginx', 'apache2-utils', 'openssl', 'curl', 'iproute2'])
        self.install_repo('central')
        # Guest-side blackhole routes prevent libvirt's privileged physical
        # management bridge from accidentally becoming a site ingress route.
        # No host firewall or routing tables are touched by this step.
        block = '''#!/bin/sh
set -eu
ip route replace blackhole 10.10.1.0/24
ip route replace blackhole 10.10.2.0/24
'''
        self.scp_bytes('central', block.encode(), '/usr/local/sbin/kvm-lab-central-isolation', '0755')
        unit = '''[Unit]
Description=Block direct E2E central-to-site LAN connectivity
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/kvm-lab-central-isolation
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
'''
        self.scp_bytes('central', unit.encode(), '/etc/systemd/system/kvm-lab-central-isolation.service')
        self.ssh('central', 'sudo systemctl daemon-reload\n'
                 'sudo systemctl enable --now kvm-lab-central-isolation.service\n')
        self.ssh('central', 'sudo /opt/kvm-vm/tools/setup-central-monitoring.sh up\n', timeout=600)
        ca, cert, key = self.generate_certs()
        self.scp_bytes('central', cert, '/etc/nginx/kvm-lab/server.pem')
        self.scp_bytes('central', key, '/etc/nginx/kvm-lab/server.key', '0600')
        self.ssh('central', 'sudo touch /etc/nginx/kvm-lab/htpasswd\n'
                 'sudo chmod 0644 /etc/nginx/kvm-lab/htpasswd\n'
                 'sudo chmod 0755 /etc/nginx/kvm-lab\n')
        for host in HOSTS:
            creds = self.data['credentials'][host]
            user = creds['host_id']
            # Stream a root-readable password file over SSH stdin, never argv.
            path = '/root/kvm-lab-' + host + '.pwd'
            self.scp_bytes('central', (creds['password'] + '\n').encode(), path, '0600')
            self.ssh('central', f"sudo sh -c 'htpasswd -B -i /etc/nginx/kvm-lab/htpasswd {user} < {path}'\n"
                     f'sudo rm -f {path}\n')
        nginx = '''server {
    listen 172.30.0.10:443 ssl;
    server_name _;
    ssl_certificate /etc/nginx/kvm-lab/server.pem;
    ssl_certificate_key /etc/nginx/kvm-lab/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    location = /api/v1/write {
        limit_except POST { deny all; }
        auth_basic "KVM E2E Remote Write";
        auth_basic_user_file /etc/nginx/kvm-lab/htpasswd;
        client_max_body_size 32m;
        proxy_read_timeout 60s;
        proxy_pass http://127.0.0.1:8428/api/v1/write;
    }
    location / { return 404; }
}
'''
        self.scp_bytes('central', nginx.encode(), '/etc/nginx/sites-available/kvm-lab.conf')
        self.ssh('central', 'sudo ln -sfn /etc/nginx/sites-available/kvm-lab.conf '
                 '/etc/nginx/sites-enabled/kvm-lab.conf\n'
                 'sudo rm -f /etc/nginx/sites-enabled/default\n'
                 'sudo nginx -t\nsudo systemctl restart nginx\n', timeout=90)
        inventory = json.loads(self.exec_guest(
            'central', 'sudo /opt/kvm-vm/tools/setup-central-monitoring.sh inventory list'))
        enrolled = {h['host_id'] for h in inventory}
        for host in HOSTS:
            c = self.data['credentials'][host]
            if c['host_id'] not in enrolled:
                self.ssh('central', f'sudo /opt/kvm-vm/tools/setup-central-monitoring.sh inventory add '
                         f'{c["host_id"]} {host} {"site-a" if host == "host-a" else "site-b"}\n')
        return ca

    def bootstrap_host(self, host, ca):
        self.docker_packages(host, ['libvirt-daemon-system', 'libvirt-clients',
                                    'qemu-system-x86', 'qemu-utils', 'curl', 'iproute2'])
        self.ssh(host, 'sudo systemctl enable --now libvirtd || sudo systemctl enable --now virtqemud\n'
                 'sudo virsh -c qemu:///system uri\n')
        self.install_repo(host)
        self.scp_bytes(host, ca, '/etc/kvm-vm/monitoring/lab-ca.pem')
        self.scp_bytes(host, (self.data['credentials'][host]['password'] + '\n').encode(),
                       '/root/kvm-e2e-password', '0600')
        c = self.data['credentials'][host]
        env = {'KVM_VM_REMOTE_WRITE_URL': 'https://172.30.0.10/api/v1/write',
               'KVM_VM_REMOTE_PASSWORD_FILE': '/root/kvm-e2e-password',
               'KVM_VM_MONITOR_HOST_ID': c['host_id'], 'KVM_VM_MONITOR_HOST': host,
               'KVM_VM_MONITOR_SITE': 'site-a' if host == 'host-a' else 'site-b'}
        environment = ' '.join(f'{k}={shlex.quote(v)}' for k, v in env.items())
        mode = 'agent' if host == 'host-a' else 'hybrid'
        self.ssh(host, f'sudo env {environment} /opt/kvm-vm/tools/setup-monitoring.sh '
                      f'render /etc/kvm-vm/monitoring --mode {mode}\n')
        # Lab-only, trusted private CA. Never use insecure_skip_verify.
        self.ssh(host, 'sudo python3 /opt/kvm-vm/tests/e2e/inject_lab_ca.py '
                       '/etc/kvm-vm/monitoring\n')
        self.ssh(host, f'sudo /opt/kvm-vm/tools/setup-monitoring.sh up --mode {mode}\n', timeout=900)

    def bootstrap(self):
        if os.geteuid() != 0:
            raise LabError('Bootstrap requires root for local state and test SSH keys')
        self.read_state()
        for node in NODES:
            print(f'Waiting for {node} SSH...', flush=True)
            self.ssh_wait(node, timeout=self.args.wait)
            self.ssh(node, 'sudo cloud-init status --wait\n', timeout=300)
        for router in ('router-a', 'router-b'):
            print(f'Configuring NAT at {router}...', flush=True)
            self.bootstrap_router(router)
        print('Deploying Central monitoring...', flush=True)
        ca = self.bootstrap_central()
        for host in HOSTS:
            print(f'Deploying {host} monitoring...', flush=True)
            self.bootstrap_host(host, ca)
        self.data['bootstrapped'] = True
        self.data['bootstrapped_at'] = now()
        self.write_state()
        print('Monitoring bootstrap completed. Next: lab.sh test')

    def exec_guest(self, node, cmd, timeout=120):
        return self.ssh(node, cmd + '\n', timeout=timeout).stdout.strip()

    def query(self, promql):
        cmd = 'curl --fail --silent --show-error --get --data-urlencode '
        cmd += shlex.quote('query=' + promql)
        cmd += ' http://127.0.0.1:8428/api/v1/query'
        raw = self.exec_guest('central', cmd, timeout=35)
        result = json.loads(raw)
        if result.get('status') != 'success':
            raise LabError('Central query failed')
        return result.get('data', {}).get('result', [])

    def query_range(self, promql, start, end, step=15):
        parts = ['curl --fail --silent --show-error --get']
        for value in ('query=' + promql, 'start=' + str(start),
                      'end=' + str(end), 'step=' + str(step)):
            parts.extend(['--data-urlencode', shlex.quote(value)])
        parts.append('http://127.0.0.1:8428/api/v1/query_range')
        data = json.loads(self.exec_guest('central', ' '.join(parts), timeout=40))
        if data.get('status') != 'success':
            raise LabError('Central range query failed')
        return data['data'].get('result', [])

    def until(self, predicate, seconds, description, pause=5):
        expiry = time.monotonic() + seconds
        err = None
        while time.monotonic() < expiry:
            try:
                if predicate():
                    return
            except (LabError, json.JSONDecodeError) as exc:
                err = str(exc)
            time.sleep(pause)
        raise LabError(f'Timeout waiting for {description}' + (f': {err}' if err else ''))

    def ensure_connected(self):
        for node in NODES:
            self.ssh(node, 'true\n', timeout=15)

    def scenario_connectivity(self):
        self.ensure_connected()
        for host in HOSTS:
            code = self.exec_guest(host,
                'curl --cacert /etc/kvm-vm/monitoring/lab-ca.pem -sS -o /dev/null -w "%{http_code}" '
                '--connect-timeout 10 https://172.30.0.10/api/v1/write')
            if code not in ('401', '403', '405'):
                raise LabError(f'{host}: ingress GET unexpectedly returned {code}')
        # Block return-to-site routes *inside Central*; the physical
        # hypervisor remains an out-of-band controller for guest provisioning.
        r = self.ssh('central', 'ip -4 route show 10.10.1.0/24\n').stdout
        if 'blackhole 10.10.1.0/24' not in r:
            raise LabError(f'Central site isolation route missing: {r}')
        # A WAN endpoint must not open direct site ingress: test the actual TCP path.
        for target in ('10.10.1.10', '10.10.2.10'):
            cmd = f'timeout 4 bash -c "echo >/dev/tcp/{target}/22"'
            result = self.ssh('central', cmd + '\n', check=False, timeout=10)
            if result.returncode == 0:
                raise LabError(f'Central can directly open site SSH {target}: WAN isolation is broken')

    def scenario_double_nat(self):
        for host in HOSTS:
            idx = '1' if host == 'host-a' else '2'
            route = self.exec_guest(host, 'ip route show default')
            if f'via 10.10.{idx}.2' not in route:
                raise LabError(f'{host} must use its own site NAT gateway: {route}')
            router = host.replace('host-', 'router-')
            nat = self.exec_guest(router, 'sudo iptables -t nat -S POSTROUTING')
            if f'-s 10.10.{idx}.0/24 -o wan0 -j MASQUERADE' not in nat:
                raise LabError(f'{router} NAT masquerade is missing')
        wan = self.virsh('net-dumpxml', self.names['wan']).stdout
        if not any(x.attrib.get('mode') == 'nat' for x in ET.fromstring(wan).findall('forward')):
            raise LabError('Outer libvirt WAN NAT is missing')

    def scenario_latency_loss(self):
        self.scenario_metrics()
        self.fault('host-a', 'netem', loss=1, latency=150, rate='1mbit')
        impaired_at = int(time.time())
        try:
            uid = self.data['credentials']['host-a']['host_id']
            self.until(lambda: bool(self.query(
                f'timestamp(kvm_vm_collector_success{{host_id="{uid}"}}) > {impaired_at + 15}')),
                180, 'recent sample over impaired WAN')
            qdisc = self.exec_guest('router-a', 'sudo tc qdisc show dev wan0')
            if 'netem' not in qdisc:
                raise LabError('Network impairment was not applied to router')
        finally:
            self.heal('host-a')

    def scenario_mode_switch(self):
        host = 'host-b'
        before = self.exec_guest(host, 'sudo cat /etc/kvm-vm/monitoring/remote.json')
        config = '/opt/kvm-vm/tools/setup-monitoring.sh'
        try:
            self.exec_guest(host, f'sudo {config} down --mode hybrid', timeout=180)
            self.exec_guest(host, f'sudo {config} up --mode agent', timeout=900)
            self.until(lambda: self.exec_guest(host, 'sudo cat /etc/kvm-vm/monitoring/.mode') == 'agent',
                       90, 'temporary Agent mode')
        finally:
            mode = self.exec_guest(host,
                'sudo cat /etc/kvm-vm/monitoring/.mode 2>/dev/null || true')
            if mode == 'agent':
                self.exec_guest(host, f'sudo {config} down --mode agent', timeout=180)
            self.exec_guest(host, f'sudo {config} up --mode hybrid', timeout=900)
        after = self.exec_guest(host, 'sudo cat /etc/kvm-vm/monitoring/remote.json')
        if json.loads(before)['host_id'] != json.loads(after)['host_id']:
            raise LabError('Host UUID changed during Agent/Hybrid mode switch')
        self.scenario_metrics()

    def scenario_metrics(self):
        for host in HOSTS:
            uid = self.data['credentials'][host]['host_id']
            self.until(lambda uid=uid: bool(self.query(f'kvm_vm_collector_success{{host_id="{uid}"}}')),
                       180, f'{host} VM collector metrics')
            self.until(lambda uid=uid: bool(self.query(f'node_memory_MemAvailable_bytes{{host_id="{uid}"}}')),
                       180, f'{host} node exporter metrics')

    def scenario_same_name_vms(self):
        xml = '''<domain type="kvm"><name>e2e-web01</name><memory unit="MiB">256</memory>
<vcpu>1</vcpu><os><type arch="x86_64">hvm</type><boot dev="hd"/></os>
<devices><emulator>/usr/bin/qemu-system-x86_64</emulator>
<graphics type="none"/></devices></domain>'''
        for host in HOSTS:
            kvm = self.ssh(host, 'test -c /dev/kvm\n', check=False)
            if kvm.returncode != 0:
                raise LabError(f'{host}: Nested KVM unavailable; same-name running VM E2E cannot pass')
            self.scp_bytes(host, xml.encode(), '/tmp/kvm-lab-guest.xml')
            self.ssh(host, 'sudo virsh define /tmp/kvm-lab-guest.xml\n'
                           'sudo virsh start e2e-web01 || sudo virsh domstate e2e-web01\n')
        self.until(lambda: len({x.get('metric', {}).get('host_id') for x in
                  self.query('kvm_vm_domain_vcpus{vm="e2e-web01"}')}) == 2,
                  150, 'same-name nested VMs separated by host_id')

    def fault(self, host, what, *, loss=1, latency=150, rate='1mbit'):
        if host not in HOSTS:
            raise LabError('Fault target must be host-a or host-b')
        router = host.replace('host-', 'router-')
        if what == 'disconnect':
            # A forwarding filter on a disposable router VM; never modify the
            # physical hypervisor's firewall or the central ingress.
            cmd = ('sudo iptables -C FORWARD -d 172.30.0.10 -p tcp --dport 443 -j REJECT '
                   '2>/dev/null || sudo iptables -I FORWARD 1 -d 172.30.0.10 '
                   '-p tcp --dport 443 -j REJECT')
        elif what == 'netem':
            if not 0 <= loss <= 100 or not 0 <= latency <= 5000 or not re.fullmatch(r'[1-9][0-9]*(?:kbit|mbit)', rate):
                raise LabError('Invalid loss, delay or rate')
            cmd = f'sudo tc qdisc replace dev wan0 root netem delay {latency}ms loss {loss}% rate {rate}'
        else:
            raise LabError('Fault must be disconnect or netem')
        self.exec_guest(router, cmd)

    def heal(self, host):
        if host not in HOSTS:
            raise LabError('Heal target must be host-a or host-b')
        router = host.replace('host-', 'router-')
        self.ssh(router, '''sudo iptables -D FORWARD -d 172.30.0.10 -p tcp --dport 443 -j REJECT 2>/dev/null || true
sudo tc qdisc del dev wan0 root 2>/dev/null || true
''')

    def scenario_disconnect_recover(self):
        self.scenario_metrics()
        uid = self.data['credentials']['host-a']['host_id']
        outage_start = time.time()
        self.fault('host-a', 'disconnect')
        try:
            time.sleep(65)
            # Check that the bounded on-disk queue is being used. Some samples
            # could be delayed before being written; query the live Agent.
            agent = self.exec_guest('host-a', 'curl -fsS http://127.0.0.1:8429/metrics')
            queue_samples = [float(line.split()[-1]) for line in agent.splitlines()
                             if line.startswith('vmagent_remotewrite_pending_data_bytes{')
                             or line.startswith('vmagent_remotewrite_pending_data_bytes ')]
            if not queue_samples or max(queue_samples) <= 0:
                raise LabError('vmagent disk queue did not grow during outage')
            self.ssh('host-a', '''sudo docker compose --project-directory /etc/kvm-vm/monitoring \
  --env-file /etc/kvm-vm/monitoring/remote.env \
  -f /etc/kvm-vm/monitoring/compose.agent.yaml restart vmagent
''', timeout=120)
        finally:
            outage_end = time.time()
            self.heal('host-a')
        self.until(lambda: bool(self.query(f'kvm_vm_collector_success{{host_id="{uid}"}}')),
                   180, 'Agent recovery after WAN disconnect')
        # Prove historical samples from the disconnected interval were
        # eventually written. A fresh post-recovery scrape alone is insufficient.
        def replayed():
            series = self.query_range(
                f'kvm_vm_collector_success{{host_id="{uid}"}}',
                int(outage_start - 30), int(time.time()), 15)
            return any(outage_start + 20 <= float(ts) <= outage_end - 10
                       for item in series for ts, _ in item.get('values', []))
        self.until(replayed, 180, 'replayed Agent samples from outage interval')

    def scenario_auth(self):
        for host in HOSTS:
            http = self.exec_guest(host,
                'curl --cacert /etc/kvm-vm/monitoring/lab-ca.pem -sS -o /dev/null -w "%{http_code}" '
                '-X POST --data-binary x --connect-timeout 10 '
                'https://172.30.0.10/api/v1/write')
            if http != '401':
                raise LabError(f'{host}: unauthenticated POST must be 401, got {http}')
            bad = self.exec_guest(host,
                'curl --cacert /etc/kvm-vm/monitoring/lab-ca.pem -sS -o /dev/null -w "%{http_code}" '
                '-u unknown:wrong -X POST --data-binary x '
                'https://172.30.0.10/api/v1/write')
            if bad != '401':
                raise LabError(f'{host}: invalid credentials must be 401, got {bad}')

    def scenario_host_offline(self):
        self.scenario_metrics()
        host = 'host-a'
        uid = self.data['credentials'][host]['host_id']
        self.fault(host, 'disconnect')
        try:
            self.until(lambda: bool(self.query(
                f'ALERTS{{alertname="KvmVmHostOffline",alertstate="firing",host_id="{uid}"}}')),
                780, 'KvmVmHostOffline alert firing', pause=15)
        finally:
            self.heal(host)
        self.until(lambda: not self.query(
            f'ALERTS{{alertname="KvmVmHostOffline",alertstate="firing",host_id="{uid}"}}'),
            180, 'KvmVmHostOffline alert resolved', pause=10)

    def test(self, scenarios):
        self.read_state()
        if not self.data.get('bootstrapped'):
            raise LabError('Lab is not bootstrapped: run lab.sh bootstrap first')
        report = {'lab_id': self.lab_id, 'started': now(), 'results': []}
        failed = False
        try:
            for name in scenarios:
                started = time.monotonic()
                result = {'scenario': name, 'started': now()}
                print(f'[{name}] running...', flush=True)
                try:
                    getattr(self, 'scenario_' + name.replace('-', '_'))()
                    result['status'] = 'PASS'
                except Exception as exc:
                    failed = True
                    result['status'] = 'FAIL'
                    result['error'] = f'{type(exc).__name__}: {str(exc)[:500]}'
                result['elapsed_seconds'] = round(time.monotonic() - started, 1)
                report['results'].append(result)
                print(f'[{name}] {result["status"]}', flush=True)
        finally:
            report['finished'] = now()
            reports = self.home / 'reports'
            reports.mkdir(mode=0o700, exist_ok=True)
            dest = reports / ('e2e-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
            dest.write_text(json.dumps(report, indent=2) + '\n')
            os.chmod(dest, 0o600)
            print(f'Report: {dest}')
        if failed:
            raise LabError('One or more live E2E scenarios failed; see report')

    def status(self):
        self.read_state()
        print('Lab:', self.lab_id, 'bootstrapped:', self.data.get('bootstrapped', False))
        for name in self.names.values():
            print('network', name, 'defined' if self.is_defined('net', name) else 'missing')
        for node in NODES:
            name = domain_name(self.lab_id, node)
            print('domain', name, 'defined' if self.is_defined('domain', name) else 'missing',
                  'SSH:', NODES[node].get('lan', NODES[node].get('wan')))

    def down(self, purge=False):
        if os.geteuid() != 0:
            raise LabError('Teardown requires root')
        self.read_state()
        for node in reversed(list(NODES)):
            name = domain_name(self.lab_id, node)
            if not self.is_defined('domain', name):
                continue
            self.assert_owned_domain(name)
            self.virsh('destroy', name, check=False)
            self.virsh('undefine', name, '--nvram', check=False)
            if self.is_defined('domain', name):
                self.virsh('undefine', name)
        for name in reversed(list(self.names.values())):
            if not self.is_defined('net', name):
                continue
            cfg = topo(self.lab_id)['networks'][name]
            self.assert_owned_network(name, cfg['bridge'], cfg['mode'])
            self.virsh('net-destroy', name, check=False)
            self.virsh('net-undefine', name)
        # Clean only filenames of resources created by this lab inside the
        # manifest-owned state directory. Keep logs and keys unless --purge.
        for node in NODES:
            name = domain_name(self.lab_id, node)
            for suffix in ('.qcow2', '-seed.img'):
                (self.image_dir / (name + suffix)).unlink(missing_ok=True)
        self.data['bootstrapped'] = False
        (self.home / 'known_hosts').unlink(missing_ok=True)
        self.write_state()
        if purge:
            # Do not follow symlinks and never delete the source cloud image.
            if self.image_dir != self.home:
                self.image_dir.rmdir()
            shutil.rmtree(self.home)
        print('Lab domains and networks removed; diagnostics ' + ('purged' if purge else 'preserved'))


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--lab-id', default='cm', help='1-12 lowercase alphanumeric characters')
    ap.add_argument('--wait', type=int, default=300, help='SSH boot wait (seconds)')
    cmds = ap.add_subparsers(dest='cmd', required=True)
    cmds.add_parser('plan', help='Show topology (no side effects or root needed)')
    up = cmds.add_parser('up', help='Provision isolated networks and five VMs')
    up.add_argument('--image', type=Path, default=Path(os.environ.get('KVM_VM_E2E_IMAGE', '/nonexistent')))
    up.add_argument('--no-bootstrap', action='store_true', help='Only create networks/VMs, then bootstrap separately')
    cmds.add_parser('bootstrap', help='Install Docker, central stack and Agent/Hybrid inside lab VMs')
    cmds.add_parser('status')
    test = cmds.add_parser('test')
    test.add_argument('scenario', nargs='?', choices=['smoke', 'all', *SCENARIOS], default='smoke')
    f = cmds.add_parser('fault', help='Inject fault inside site router')
    f.add_argument('host', choices=HOSTS)
    f.add_argument('type', choices=['disconnect', 'netem'])
    f.add_argument('--loss', type=int, default=1)
    f.add_argument('--latency', type=int, default=150)
    f.add_argument('--rate', default='1mbit')
    heal = cmds.add_parser('heal')
    heal.add_argument('host', choices=HOSTS)
    down = cmds.add_parser('down')
    down.add_argument('--purge', action='store_true', help='Also delete state, keys and reports')
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not re.fullmatch(r'[a-z0-9]{1,12}', args.lab_id):
        ap.error('--lab-id must match [a-z0-9]{1,12}')
    lab = Lab(args)
    try:
        if args.cmd == 'plan':
            print(json.dumps(topo(args.lab_id), indent=2))
        elif args.cmd == 'up':
            lab.up(args.image)
            if not args.no_bootstrap:
                lab.bootstrap()
        elif args.cmd == 'bootstrap':
            lab.bootstrap()
        elif args.cmd == 'status':
            lab.status()
        elif args.cmd == 'test':
            which = ('connectivity', 'metrics', 'auth') if args.scenario == 'smoke' else (
                SCENARIOS if args.scenario == 'all' else (args.scenario,))
            lab.test(which)
        elif args.cmd == 'fault':
            lab.read_state()
            lab.fault(args.host, args.type, loss=args.loss, latency=args.latency, rate=args.rate)
        elif args.cmd == 'heal':
            lab.read_state()
            lab.heal(args.host)
        elif args.cmd == 'down':
            lab.down(args.purge)
        return 0
    except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
