"""Opt-in console acceptance controller; credentials stay in memory or private host files."""
import argparse
import json
from pathlib import Path
import re
import secrets
import shlex
import socket
import stat
import subprocess
import time
import uuid

import paramiko
import yaml


class Acceptance:
    @property
    def phase(self):
        return self._phase

    @phase.setter
    def phase(self, value):
        self._phase = value
        print("Checking: " + value, flush=True)

    def __init__(self, args):
        self.args = args
        self.host = paramiko.SSHClient()
        self.host.load_system_host_keys()
        user, address = args.host.rsplit("@", 1)
        self.host.connect(address, port=args.host_port, username=user, key_filename=args.key,
                          allow_agent=False, look_for_keys=False, timeout=15)
        self.sftp = self.host.open_sftp()
        self.prefix = "e2e-console-" + uuid.uuid4().hex[:8]
        self.directory = "/root/" + self.prefix
        self.command("install", "-d", "-m", "0700", self.directory)
        self.public_key = subprocess.check_output(["ssh-keygen", "-y", "-f", args.key], text=True).strip()
        self.guests = set()
        self.outputs = []
        self.phase = "initialization"

    def command(self, *args, check=True, timeout=900):
        stdin, stdout, stderr = self.host.exec_command(shlex.join(args), timeout=timeout)
        stdin.close()
        output, error = stdout.read().decode(), stderr.read().decode()
        code = stdout.channel.recv_exit_status()
        if check and code:
            raise RuntimeError("Host command failed: " + args[0])
        return code, output, error

    def cli(self, *args):
        env = ["env"]
        for key in ("NAME", "VCPUS", "MEMORY", "MEMORY_MIB", "DISK", "DISK_GIB", "BRIDGE", "MACVTAP", "IPV4", "GATEWAY", "DNS", "OS"):
            env += ["-u", "KVM_VM_" + key]
        deadline = time.monotonic() + self.args.timeout
        while True:
            result = self.command(*env, "python3", self.args.remote_cli, *args, check=False)
            self.outputs.extend(result[1:])
            if not result[0]:
                return result
            # This CLI rejection happens before any credential or VM mutation.
            # Never retry a provisioning failure or remove another operation's lock.
            if "Another lifecycle operation or interrupted attempt owns" not in result[2]:
                raise RuntimeError("CLI operation failed")
            if time.monotonic() >= deadline:
                raise RuntimeError("Lifecycle lock wait timed out")
            time.sleep(3)

    def write(self, path, content):
        with self.sftp.open(path, "w") as output:
            output.write(content)

    def definition(self, name, distro, enabled, address=None, image_path=None):
        network = {"mode": self.args.mode}
        if self.args.mode == "nat":
            network.update(libvirt_network="default", ipv4={"method": "dhcp"})
        else:
            network["parent" if self.args.mode == "macvtap" else "bridge"] = self.args.attachment
            network["ipv4"] = {"method": "static", "address": address or self.args.address,
                               "gateway": self.args.gateway, "dns": ["1.1.1.1"]}
        raw = {"version": 1, "vm": {"name": name, "vcpus": 2, "memory_mib": 2048},
               "storage": {"disk_gib": 20, "image": {"path": image_path} if image_path else {"distro": distro}}, "network": network,
               "cloud_init": {"user": self.args.user,
                              "ssh_authorized_keys": [self.public_key], "qemu_guest_agent": True}}
        if enabled is not None:
            raw["cloud_init"]["console_password"] = enabled
        path = self.directory + "/" + name + ".yaml"
        self.write(path, yaml.safe_dump(raw))
        return path

    def credential(self, path):
        info = self.sftp.stat(path)
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != 0:
            raise RuntimeError("Credential ownership/permissions failed")
        directory = self.sftp.stat(self.directory)
        if stat.S_IMODE(directory.st_mode) != 0o700 or directory.st_uid != 0:
            raise RuntimeError("Credential directory is not private")
        with self.sftp.open(path) as source:
            return json.loads(source.read())

    def addresses(self, name, configured=None):
        if self.args.mode != "nat":
            return [(configured or self.args.address).split("/")[0]]
        addresses = []
        for source in ("agent", "lease"):
            code, output, _ = self.command("virsh", "domifaddr", name, "--source", source, check=False)
            if code:
                continue
            for address in re.findall(r"ipv4\s+(\d+\.\d+\.\d+\.\d+)/", output):
                if not address.startswith("127.") and address not in addresses:
                    addresses.append(address)
        return addresses

    def connection(self, address):
        if self.args.mode == "nat":
            return self.host.get_transport().open_channel("direct-tcpip", (address, 22), ("127.0.0.1", 0), timeout=15)
        return socket.create_connection((address, 22), timeout=15)

    def ssh(self, address):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(address, username=self.args.user, key_filename=self.args.key,
                       sock=self.connection(address), allow_agent=False, look_for_keys=False,
                       timeout=15, banner_timeout=15, auth_timeout=15)
        return client

    @staticmethod
    def guest_command(client, command, timeout=300):
        stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        stdin.close()
        output = stdout.read().decode()
        stderr.read()
        if stdout.channel.recv_exit_status():
            raise RuntimeError("Guest command did not complete successfully")
        return output.strip()

    def ready(self, name, configured=None):
        deadline = time.monotonic() + self.args.timeout
        while time.monotonic() < deadline:
            for address in self.addresses(name, configured):
                try:
                    client = self.ssh(address)
                except (OSError, EOFError, paramiko.SSHException):
                    continue
                try:
                    if self.guest_command(client, "hostname") != name:
                        raise RuntimeError("Address belongs to an unexpected guest")
                    self.guest_command(client, "sudo timeout 300 cloud-init status --wait", timeout=315)
                    return address
                finally:
                    client.close()
            time.sleep(5)
        raise RuntimeError("Guest SSH readiness timeout")

    def serial(self, name, credential, reject=False):
        args = ["python3", self.args.remote_probe, name, "--credential", credential]
        if reject:
            args.append("--reject")
        code, output, _ = self.command(*args, check=False, timeout=150)
        if code:
            # Probe output is intentionally restricted to a phase and status.
            raise RuntimeError("Serial probe failed: " + output.strip())
        return json.loads(output)

    def verify(self, name, address, credential, enabled):
        self.phase = "credential permissions"
        delivered = self.credential(credential)
        self.phase = "serial login"
        self.serial(name, credential, reject=not enabled)
        self.phase = "SSH key and account policy"
        client = self.ssh(address)
        try:
            server_key = client.get_transport().get_remote_server_key()
            if self.guest_command(client, "id -un") != self.args.user:
                raise RuntimeError("Unexpected SSH account")
            status = self.guest_command(client, "sudo passwd -S " + shlex.quote(self.args.user)).split()[1]
            if enabled and status not in ("P", "PS"):
                raise RuntimeError("Enabled account remained locked")
            if not enabled and status not in ("L", "LK"):
                raise RuntimeError("Disabled account was not locked")
            root_status = self.guest_command(client, "sudo passwd -S root").split()[1]
            if root_status not in ("L", "LK", "NP"):
                raise RuntimeError("Unexpected root password state")
            getty = self.guest_command(client, "systemctl show serial-getty@ttyS0.service -p ActiveState -p ExecStart")
            if "ActiveState=active" not in getty or "--autologin" in getty:
                raise RuntimeError("Serial getty is inactive or enables autologin")
            if enabled:
                expiry = self.guest_command(client, "sudo getent shadow " + shlex.quote(self.args.user) + " | cut -d: -f3,5,7,8").split(":")
                if len(expiry) != 4 or expiry[0] in ("", "0") or any(value not in ("", "-1") for value in expiry[1:]):
                    raise RuntimeError("Unexpected password expiry or forced first-login change")
            versions = self.guest_command(client, "cloud-init --version 2>&1; if command -v python3 >/dev/null; then python3 --version; else /usr/libexec/platform-python --version; fi; getconf GNU_LIBC_VERSION; if command -v rpm >/dev/null; then rpm -q pam shadow-utils openssh-server; else dpkg-query -W libpam0g passwd openssh-server; fi")
            peer = self.guest_command(client, "printf '%s' \"$SSH_CONNECTION\"").split()[0]
            context = "user=" + self.args.user + ",host=" + peer + ",addr=" + peer
            policy = self.guest_command(client, "sudo /usr/sbin/sshd -T -C " + shlex.quote(context))
            if enabled and not all(value in policy.splitlines() for value in
                                   ("passwordauthentication no", "kbdinteractiveauthentication no")):
                raise RuntimeError("Effective SSH password policy is not disabled")
            logs = self.guest_command(client, "sudo cat /var/log/cloud-init.log /var/log/cloud-init-output.log")
            if delivered["password"] in logs:
                raise RuntimeError("Credential leaked into provisioning logs")
        finally:
            client.close()
        for method in ("password", "keyboard-interactive"):
            self.phase = "SSH " + method + " rejection"
            transport = paramiko.Transport(self.connection(address))
            transport.auth_timeout = 30
            try:
                transport.start_client(timeout=15)
                if transport.get_remote_server_key() != server_key:
                    raise RuntimeError("SSH server identity changed during authentication checks")
                try:
                    if method == "password":
                        transport.auth_password(self.args.user, delivered["password"], fallback=False)
                    else:
                        transport.auth_interactive(self.args.user, lambda title, instructions, prompts: [delivered["password"] for _ in prompts])
                except paramiko.AuthenticationException:
                    pass
                else:
                    raise RuntimeError("SSH accepted a password-capable authentication method")
            finally:
                transport.close()
        self.phase = "plaintext artifact scan"
        xml = self.command("virsh", "dumpxml", name)[1]
        if "<serial type='pty'>" not in xml or "<target type='serial' port='0'/>" not in xml:
            raise RuntimeError("Missing expected serial hardware")
        password = delivered["password"].encode()
        for output in self.outputs:
            if password in output.encode():
                raise RuntimeError("Credential leaked into CLI output")
        paths = ["/var/lib/kvm-vm/state/" + name + ".json", "/etc/kvm-vm/definitions/" + name + ".yaml"]
        cloud = "/var/lib/libvirt/images/cloud-init/" + name
        paths += [cloud + "/" + item for item in self.sftp.listdir(cloud)]
        for path in paths:
            with self.sftp.open(path) as source:
                source.prefetch()
                if password in source.read():
                    raise RuntimeError("Credential leaked into managed artifacts")
        return {"serial": "pass", "sudo": "pass" if enabled else "not applicable",
                "ssh_key": "pass", "ssh_password_rejection": "pass", "ssh_keyboard_interactive_rejection": "pass",
                "credential_permissions": "pass", "plaintext_scan": "pass", "versions": versions,
                "provisioning_log_scan": "pass",
                "serial_hardware_getty": "pass", "root_password_autologin": "pass",
                "expiry_policy": "pass" if enabled else "not applicable"}

    def delete(self, name):
        self.phase = "delete " + name
        self.cli("delete", name, "--force", "--yes")
        self.guests.discard(name)

    def stop(self, name):
        self.command("virsh", "shutdown", name)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if self.command("virsh", "domstate", name)[1].strip() == "shut off":
                return
            time.sleep(3)
        raise RuntimeError("Graceful shutdown timeout")

    def reboot_check(self, name, credential, enabled):
        self.phase = "reboot " + name
        address = self.ready(name)
        client = self.ssh(address)
        try:
            before = self.guest_command(client, "cat /proc/sys/kernel/random/boot_id")
        finally:
            client.close()
        self.stop(name)
        self.command("virsh", "start", name)
        address = self.ready(name)
        client = self.ssh(address)
        try:
            after = self.guest_command(client, "cat /proc/sys/kernel/random/boot_id")
        finally:
            client.close()
        if before == after:
            raise RuntimeError("Boot identity did not change")
        result = self.verify(name, address, credential, enabled)
        result["boot_identity_changed"] = True
        return result

    def lifecycle(self, source, distro, source_credential, results):
        """Exercise real offline clones and reinstall; retain everything on failure."""
        self.stop(source)
        targets = []
        delivered_files = {source_credential}
        for suffix, enabled in (("enabled", True), ("disabled", False)):
            name = self.prefix + "-" + suffix
            definition = self.definition(name, distro, enabled)
            credential = self.directory + "/" + name + ".json" if enabled else source_credential
            self.guests.add(name)
            self.phase = "clone " + suffix
            args = ["clone", source, definition]
            if enabled:
                args += ["--console-password-file", credential]
            self.cli(*args)
            delivered_files.add(credential)
            address = self.ready(name)
            if enabled and self.credential(credential)["password"] == self.credential(source_credential)["password"]:
                raise RuntimeError("Clone reused source credential")
            self.verify(name, address, credential, enabled)
            self.serial(name, source_credential, reject=True)
            result = {"scenario": "enabled source to " + suffix, "status": "pass",
                      "source_password_rejected": True,
                      "after_reboot": self.reboot_check(name, credential, enabled)}
            results.append(result)
            targets.append((name, credential, enabled))
        self.command("virsh", "start", source)
        self.verify(source, self.ready(source), source_credential, True)
        results.append({"scenario": "source credential preserved", "status": "pass"})
        disabled = targets[1][0]
        self.stop(disabled)
        name = self.prefix + "-from-disabled"
        credential = self.directory + "/" + name + ".json"
        self.guests.add(name)
        self.phase = "disabled source to enabled clone"
        self.cli("clone", disabled, self.definition(name, distro, True), "--console-password-file", credential)
        delivered_files.add(credential)
        self.verify(name, self.ready(name), credential, True)
        self.serial(name, source_credential, reject=True)
        results.append({"scenario": "disabled source to enabled", "status": "pass",
                        "after_reboot": self.reboot_check(name, credential, True)})
        targets.append((name, credential, True))
        self.command("virsh", "start", disabled)
        for name, old_credential, enabled in targets[:2]:
            self.phase = "reinstall " + ("enabled" if enabled else "disabled")
            credential = self.directory + "/" + name + "-reinstalled.json" if enabled else old_credential
            args = ["reinstall", name, "--force", "--yes"]
            if enabled:
                args += ["--console-password-file", credential]
            self.cli(*args)
            delivered_files.add(credential)
            self.verify(name, self.ready(name), credential, enabled)
            self.serial(name, old_credential, reject=True)
            results.append({"scenario": "reinstall " + ("enabled" if enabled else "disabled"),
                            "status": "pass", "old_password_rejected": True,
                            "after_reboot": self.reboot_check(name, credential, enabled)})
        for name, credential, enabled in targets:
            self.delete(name)
        for credential in delivered_files:
            self.credential(credential)
        results.append({"scenario": "delete preserves all delivered credentials", "status": "pass"})

    def close(self):
        self.sftp.close()
        self.host.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "key", "remote-cli", "remote-probe", "report"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--host-port", type=int, default=22)
    parser.add_argument("--distros", default="debian13")
    parser.add_argument("--mode", choices=("nat", "bridge", "macvtap"), default="nat")
    parser.add_argument("--attachment")
    parser.add_argument("--address")
    parser.add_argument("--gateway")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--locked-default", action="store_true")
    parser.add_argument("--preexisting-user", action="store_true", help="Exercise an existing account, preparing a private image copy if the cached image lacks it")
    parser.add_argument("--lifecycle", action="store_true", help="Exercise clone/reinstall/reboot on NAT enabled cases")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute or (args.mode != "nat" and not all((args.attachment, args.address, args.gateway))):
        parser.error("Explicit execution and complete non-NAT network inputs are required")
    if args.lifecycle and (args.mode != "nat" or args.locked_default):
        parser.error("Lifecycle matrix requires NAT and an enabled source")
    session = Acceptance(args)
    report = {"mode": args.mode, "credential_directory": session.directory, "cases": [], "status": "running",
              "runtime_sha256": session.command("sha256sum", args.remote_cli)[1].split()[0]}
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    try:
        for index, distro in enumerate(args.distros.split(",")):
            name = session.prefix + "-" + str(index)
            enabled = not args.locked_default
            definition = session.definition(name, distro, True if enabled else None)
            credential = session.directory + "/" + name + ".json"
            case = {"distro": distro, "name": name, "user": args.user, "status": "running"}
            report["cases"].append(case)
            case["console_enabled"] = enabled
            case["console_policy"] = "enabled" if enabled else "omitted (default)"
            code = "import runpy,sys; print(runpy.run_path(sys.argv[1])['DISTROS'][sys.argv[2]]['cache'])"
            cache = session.command("python3", "-c", code, args.remote_cli, distro)[1].strip()
            base = "/var/lib/libvirt/images/base/" + cache
            case["cached_image_sha256"] = session.command("sha256sum", base)[1].split()[0]
            fixture = None
            if args.preexisting_user:
                passwd = session.command("virt-cat", "-a", base, "/etc/passwd")[1]
                if not any(line.startswith(args.user + ":") for line in passwd.splitlines()):
                    session.phase = "prepare isolated preexisting-account fixture"
                    fixture = session.directory + "/" + name + "-preexisting.qcow2"
                    case["preexisting_fixture"] = fixture
                    session.command("qemu-img", "convert", "-f", "qcow2", "-O", "qcow2", base, fixture)
                    session.command("virt-customize", "-a", fixture, "--run-command",
                                    "useradd --create-home --shell /bin/bash " + shlex.quote(args.user))
                    passwd = session.command("virt-cat", "-a", fixture, "/etc/passwd")[1]
                    if not any(line.startswith(args.user + ":") for line in passwd.splitlines()):
                        raise RuntimeError("Fixture account was not present before provisioning")
                    case["fixture_sha256"] = session.command("sha256sum", fixture)[1].split()[0]
                    definition = session.definition(name, distro, True if enabled else None, image_path=fixture)
                case["preexisting_base_user"] = True
            print("Creating " + distro + " " + args.mode + " " + name, flush=True)
            session.guests.add(name)
            report["retained_guests"] = sorted(session.guests)
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            session.phase = "create"
            if enabled:
                session.cli("create", definition, "--console-password-file", credential)
            else:
                session.cli("create", definition)
                with session.sftp.open(credential, "wx") as output:
                    output.chmod(0o600)
                    output.write(json.dumps({"vm": name, "user": args.user, "password": secrets.token_urlsafe(24)}))
            session.phase = "SSH readiness and cloud-init"
            address = session.ready(name)
            case.update(session.verify(name, address, credential, enabled), status="pass")
            case["after_reboot"] = session.reboot_check(name, credential, enabled)
            if args.lifecycle:
                case["lifecycle"] = []
                session.lifecycle(name, distro, credential, case["lifecycle"])
            session.delete(name)
            session.credential(credential)  # Deletion must preserve explicit delivery.
            case["delete_preserves_credential"] = "pass"
            if fixture:
                session.sftp.remove(fixture)
                case["preexisting_fixture_cleanup"] = "pass"
            report["retained_guests"] = sorted(session.guests)
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print("Console and SSH authentication passed: " + distro, flush=True)
        report["status"] = "pass"
    except (Exception, KeyboardInterrupt) as exc:
        report["status"] = "failed"
        if report["cases"]:
            report["cases"][-1]["status"] = "failed"
        # Do not serialize arbitrary exception text or terminal/network buffers.
        report["failure_type"] = type(exc).__name__
        report["failure_phase"] = session.phase
        report["retained_guests"] = sorted(session.guests)
        print("Acceptance failed; guest and credential artifacts retained (diagnostics redacted)", flush=True)
    finally:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        session.close()
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
