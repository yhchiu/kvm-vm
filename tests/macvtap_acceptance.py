"""Opt-in Macvtap CLI acceptance, run from a separate network client.

See tests/MACVTAP_ACCEPTANCE.md. No host network configuration is changed.
Failures retain uniquely named guests for diagnosis; success removes them.
"""
import argparse
import ipaddress
import json
import re
from pathlib import Path
import shlex
import subprocess
import time
import uuid
import xml.etree.ElementTree as ET

import yaml


def acceptance_status(checks):
    if "failed" in checks.values():
        return "failed"
    if "pending" in checks.values() or False in checks.values():
        return "partial"
    return "pass"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("host", "key", "parent", "address", "peer-address", "gateway", "remote-cli", "report"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--host-port", default="22")
    parser.add_argument("--distro", default="debian13")
    parser.add_argument("--dns", default="1.1.1.1")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--dhcp", action="store_true", help="Use DHCP for source; --address must be its reserved address")
    parser.add_argument("--source-mac", help="Reserved source MAC; required with --dhcp")
    parser.add_argument("--controlled-dhcp", action="store_true", help="After static checks, test DHCP using a MAC-restricted server in the peer guest")
    parser.add_argument("--execute-with-reserved-addresses", action="store_true")
    args = parser.parse_args()
    if not args.execute_with_reserved_addresses:
        parser.error("Explicit execution acknowledgement and two reserved addresses are required")
    if args.dhcp and not args.source_mac:
        parser.error("DHCP requires --source-mac matching the external reservation")
    if args.controlled_dhcp and (args.dhcp or not args.distro.startswith(("debian", "ubuntu"))):
        parser.error("Controlled DHCP requires a static Debian/Ubuntu run")
    if args.source_mac and not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.source_mac):
        parser.error("Invalid source MAC")
    addresses = [ipaddress.ip_interface(args.address), ipaddress.ip_interface(args.peer_address)]
    if any(item.version != 4 for item in addresses) or addresses[0].ip == addresses[1].ip:
        parser.error("Two distinct reserved IPv4 addresses are required")
    if args.controlled_dhcp and addresses[0].network != addresses[1].network:
        parser.error("Controlled DHCP requires both reserved addresses in the same subnet")
    for value in (args.gateway, args.dns):
        ipaddress.IPv4Address(value)
    if any(item.ip in (item.network.network_address, item.network.broadcast_address,
                       ipaddress.IPv4Address(args.gateway)) for item in addresses):
        parser.error("Guest addresses must not be network, broadcast or gateway addresses")
    prefix = "e2e-macvtap-" + uuid.uuid4().hex[:8]
    names = [prefix + "-src", prefix + "-peer"]
    report = {"run": prefix, "host": args.host, "parent": args.parent,
              "distro": args.distro, "addressing": "dhcp-source/static-peer" if args.dhcp else "static",
              "checks": {}, "guests": names, "status": "running"}
    report_path = Path(args.report)
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-i", args.key]

    def execute(cmd, *, input_text=None, check=True, timeout=900):
        result = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError("Command failed: " + result.stderr[-1800:])
        return result

    def host(*cmd, **kwargs):
        return execute(ssh + ["-p", args.host_port, args.host, shlex.join(cmd)], **kwargs)

    def cli(*cmd, override=None):
        env = ["env"]
        for key in ("NAME", "VCPUS", "MEMORY", "MEMORY_MIB", "DISK", "DISK_GIB",
                    "BRIDGE", "MACVTAP", "IPV4", "GATEWAY", "DNS", "OS"):
            env += ["-u", "KVM_VM_" + key]
        if override:
            env += [key + "=" + value for key, value in override.items()]
        return host(*env, "python3", args.remote_cli, *cmd)

    def guest(index, command, *, check=True, timeout=30):
        return execute(ssh + ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=" + known_hosts,
                              "admin@" + str(addresses[index].ip), command], check=check, timeout=timeout)

    def wait_guest(index):
        deadline = time.monotonic() + args.timeout
        while time.monotonic() < deadline:
            result = guest(index, "hostname", check=False)
            if result.returncode == 0 and result.stdout.strip() == names[index]:
                guest(index, "sudo timeout " + str(args.timeout) + " cloud-init status --wait", timeout=args.timeout + 15)
                return
            time.sleep(5)
        raise RuntimeError("External SSH key login did not become ready for " + names[index])

    def xml_check(index):
        root = ET.fromstring(host("virsh", "dumpxml", names[index]).stdout)
        interfaces = root.findall("./devices/interface")
        assert len(interfaces) == 1, "Expected a single NIC"
        nic = interfaces[0]
        assert nic.get("type") == "direct"
        assert nic.find("source").get("dev") == args.parent
        assert nic.find("source").get("mode") == "bridge"
        assert nic.find("model").get("type") == "virtio"
        definition = yaml.safe_load(host("cat", "/etc/kvm-vm/definitions/" + names[index] + ".yaml").stdout)
        assert nic.find("mac").get("address") == definition["network"]["mac"]
        assert definition["network"]["mode"] == "macvtap"
        channels = root.findall("./devices/channel/target")
        report["checks"][names[index] + "_agent_channel"] = any(x.get("name") == "org.qemu.guest_agent.0" for x in channels)

    def parent_state():
        link = json.loads(host("ip", "-j", "address", "show", "dev", args.parent).stdout)[0]
        for address in link.get("addr_info", []):
            # Lease lifetimes count down without any interface reconfiguration.
            address.pop("valid_life_time", None)
            address.pop("preferred_life_time", None)
        return {key: link.get(key) for key in ("ifname", "flags", "mtu", "master", "address", "operstate", "addr_info")}

    # The run-owned known_hosts file isolates ephemeral guest host keys.
    known_hosts = str(report_path.with_suffix(".known_hosts").absolute())
    if Path(known_hosts).exists():
        raise RuntimeError("Choose a fresh report path")
    baseline = parent_state()
    parent_addresses = {item["local"] for item in baseline["addr_info"]}
    if any(str(item.ip) in parent_addresses for item in addresses):
        raise RuntimeError("A reserved guest address conflicts with the host parent")
    report["parent_before"] = baseline
    report["host_versions"] = host("virsh", "version").stdout
    public_key = execute(["ssh-keygen", "-y", "-f", args.key]).stdout.strip()
    remote_dir = host("mktemp", "-d", "/tmp/" + prefix + "-XXXXXX").stdout.strip()
    definitions = []
    try:
        for index, name in enumerate(names):
            network = {"mode": "macvtap", "parent": args.parent} if index == 0 else {"mode": "nat", "libvirt_network": "default"}
            if index == 0 and args.source_mac:
                network["mac"] = args.source_mac
            network["ipv4"] = ({"method": "dhcp"} if args.dhcp and index == 0 else
                               {"method": "static", "address": str(addresses[index]), "gateway": args.gateway, "dns": [args.dns]})
            raw = {"version": 1, "vm": {"name": name, "memory_mib": 1024, "vcpus": 1},
                   "storage": {"disk_gib": 12, "image": {"distro": args.distro}},
                   "network": network, "cloud_init": {"ssh_authorized_keys": [public_key], "qemu_guest_agent": True}}
            path = remote_dir + "/" + name + ".yaml"
            host("tee", path, input_text=yaml.safe_dump(raw))
            definitions.append(path)
        cli("create", definitions[0])
        print("Created " + names[0] + "; waiting for external SSH and cloud-init", flush=True)
        xml_check(0)
        wait_guest(0)
        guest(0, "ping -c 2 " + args.gateway)
        report["checks"]["create_external_key_login"] = "pass"
        print("External key login and guest gateway connectivity passed", flush=True)
        host("virsh", "shutdown", names[0])
        deadline = time.monotonic() + 90
        while host("virsh", "domstate", names[0]).stdout.strip() != "shut off":
            if time.monotonic() > deadline:
                raise RuntimeError("Source did not shut down for clone")
            time.sleep(3)
        cli("clone", names[0], definitions[1], override={"KVM_VM_MACVTAP": args.parent})
        host("virsh", "start", names[0])
        for index in (0, 1):
            xml_check(index)
            wait_guest(index)
        guest(0, "ping -c 2 " + str(addresses[1].ip))
        guest(1, "ping -c 2 " + str(addresses[0].ip))
        report["checks"]["clone_override_and_peer_connectivity"] = "pass"
        print("Clone override and guest-to-guest connectivity passed", flush=True)
        # Binding the parent excludes preexisting routed-bridge alternate paths.
        probe = host("ping", "-I", args.parent, "-c", "2", "-W", "2", str(addresses[0].ip), check=False)
        assert probe.returncode == 1, "Expected no direct host-to-guest path through parent"
        report["checks"]["host_parent_isolation"] = "pass"
        agent = host("virsh", "domifaddr", names[0], "--source", "agent", check=False)
        report["checks"]["agent_address_discovery"] = "pass" if agent.returncode == 0 and str(addresses[0].ip) in agent.stdout else "pending"
        cli("reinstall", names[0], "--force", "--yes", override={"KVM_VM_BRIDGE": "invalid-review-bridge", "KVM_VM_MACVTAP": "invalid-parent"})
        xml_check(0)
        wait_guest(0)
        old_boot = guest(0, "cat /proc/sys/kernel/random/boot_id").stdout.strip()
        host("virsh", "reboot", names[0])
        deadline = time.monotonic() + args.timeout
        while True:
            boot = guest(0, "cat /proc/sys/kernel/random/boot_id", check=False)
            if boot.returncode == 0 and boot.stdout.strip() != old_boot:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Guest did not complete a reboot")
            time.sleep(5)
        wait_guest(0)
        report["checks"]["reinstall_saved_network_and_restart"] = "pass"
        if args.controlled_dhcp:
            source_xml = ET.fromstring(host("virsh", "dumpxml", names[0]).stdout)
            source_mac = source_xml.find("./devices/interface/mac").get("address")
            guest(1, "sudo env DEBIAN_FRONTEND=noninteractive apt-get -y install dnsmasq-base", timeout=args.timeout)
            server = ["sudo", "systemd-run", "--unit=kvm-vm-dhcp-test", "--collect", "--",
                      "/usr/sbin/dnsmasq", "--keep-in-foreground", "--conf-file=/dev/null", "--port=0",
                      "--interface=eth0", "--bind-interfaces", "--dhcp-leasefile=/run/kvm-vm-dhcp.leases",
                      "--dhcp-range=" + str(addresses[0].ip) + "," + str(addresses[0].ip) + "," + str(addresses[0].netmask) + ",2m",
                      "--dhcp-host=" + source_mac + "," + str(addresses[0].ip), "--dhcp-ignore=tag:!known",
                      "--dhcp-option=3," + args.gateway, "--dhcp-option=6," + args.dns]
            guest(1, shlex.join(server))
            guest(1, "sudo systemctl is-active kvm-vm-dhcp-test")
            source_config = yaml.safe_load(host("cat", definitions[0]).stdout)
            source_config["network"]["mac"] = source_mac
            source_config["network"]["ipv4"] = {"method": "dhcp"}
            host("tee", definitions[0], input_text=yaml.safe_dump(source_config))
            cli("delete", names[0], "--force", "--yes")
            cli("create", definitions[0])
            xml_check(0)
            wait_guest(0)
            lease = guest(1, "sudo cat /run/kvm-vm-dhcp.leases").stdout
            assert source_mac in lease and str(addresses[0].ip) in lease
            guest(0, "ping -c 2 " + args.gateway)
            report["checks"]["controlled_dhcp_external_login_and_lease"] = "pass"
            report["dhcp_topology"] = "Peer Macvtap guest serves only the source MAC and reserved address; provider DHCP remains unconfirmed"
            print("Controlled DHCP lease and external key login passed", flush=True)
        for name in reversed(names):
            cli("delete", name, "--force", "--yes")
            assert host("virsh", "dominfo", name, check=False).returncode != 0
        report["checks"]["delete"] = "pass"
        report["status"] = acceptance_status(report["checks"])
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["recovery"] = "Guests and artifacts retained; inspect only the listed run-owned names before cleanup"
        raise
    finally:
        report["parent_after"] = parent_state()
        report["checks"]["parent_unchanged"] = "pass" if report["parent_after"] == baseline else "failed"
        if report["checks"]["parent_unchanged"] != "pass":
            report["status"] = "failed"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("Acceptance status: " + report["status"] + "; report: " + str(report_path))
    if report["status"] != "pass":
        raise RuntimeError("Acceptance did not fully pass; inspect failed or pending checks")


if __name__ == "__main__":
    main()
