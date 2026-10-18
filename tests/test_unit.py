"""Unit tests for kvm-vm pure-Python functions.

Run with: python3 -m pytest tests/test_unit.py -v
"""

from __future__ import annotations

import importlib.util
import io
import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from importlib.machinery import SourceFileLoader

# Import kvm-vm as a module despite the extensionless and hyphenated filename.
_SCRIPT = Path(__file__).resolve().parent.parent / "kvm-vm"
_loader = SourceFileLoader("kvm_vm", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("kvm_vm", _loader)
kvm_vm = importlib.util.module_from_spec(_spec)
sys.modules["kvm_vm"] = kvm_vm
_spec.loader.exec_module(kvm_vm)


@pytest.fixture(autouse=True)
def isolated_state_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(kvm_vm, "STATE_DIR", tmp_path / "state")


def lifecycle_probe(cmd, disk, **kwargs):
    """Read-only host responses for older orchestration tests."""
    stdout = ""
    if cmd[0] == "qemu-img":
        stdout = json.dumps({"format": "qcow2", "virtual-size": 20 * 1024**3})
    elif cmd[3] == "dumpxml":
        root = kvm_vm.ET.Element("domain")
        devices = kvm_vm.ET.SubElement(root, "devices")
        item = kvm_vm.ET.SubElement(devices, "disk", type="file", device="disk")
        kvm_vm.ET.SubElement(item, "source", file=str(disk))
        kvm_vm.ET.SubElement(item, "target", dev="vda")
        stdout = kvm_vm.ET.tostring(root, encoding="unicode")
    elif cmd[3] == "domjobinfo":
        stdout = "Job type: None"
    elif cmd[3] not in ("checkpoint-list", "snapshot-list", "blockjob", "list", "destroy"):
        raise AssertionError(cmd)
    return subprocess.CompletedProcess(cmd, 0, stdout, "")


# --- dig() ---

class TestDig:
    def test_nested_key(self):
        assert kvm_vm.dig({"a": {"b": 1}}, "a", "b") == 1

    def test_missing_key_returns_default(self):
        assert kvm_vm.dig({"a": 1}, "x", default="nope") == "nope"

    def test_non_dict_intermediate_returns_default(self):
        assert kvm_vm.dig({"a": 42}, "a", "b", default=None) is None


# --- generate_mac() ---

class TestGenerateMac:
    def test_format(self):
        mac = kvm_vm.generate_mac()
        assert mac.startswith("52:54:00:")
        assert kvm_vm.MAC_RE.match(mac)

    def test_exclude(self):
        fixed = "52:54:00:aa:bb:cc"
        alt = "52:54:00:11:22:33"
        # Return fixed first, then alt
        with patch("kvm_vm.random.randrange", side_effect=[0xAA, 0xBB, 0xCC, 0x11, 0x22, 0x33]):
            result = kvm_vm.generate_mac(exclude={fixed})
        assert result == alt

    def test_exhaustion_raises(self):
        with patch("kvm_vm.random.randrange", return_value=0xAA):
            with pytest.raises(kvm_vm.KVMError, match="Could not generate a unique MAC"):
                kvm_vm.generate_mac(exclude={"52:54:00:aa:aa:aa"})


# --- normalize_definition() ---

def _minimal_raw(**overrides):
    """Build a minimal valid raw definition dict."""
    base = {
        "version": 1,
        "vm": {"name": "test01"},
        "storage": {"image": {"distro": "ubuntu24.04"}},
        "network": {"ipv4": {"method": "dhcp"}},
        "cloud_init": {"ssh_authorized_keys": ["ssh-ed25519 AAAA... test@test"]},
    }
    base.update(overrides)
    return base


class TestNormalizeDefinition:
    def test_defaults(self):
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["vm"]["vcpus"] == 2
        assert cfg["vm"]["memory_mib"] == 2048
        assert cfg["storage"]["disk_gib"] == 20
        assert cfg["network"]["mode"] == "bridge"
        assert cfg["network"]["bridge"] == "br0"
        assert cfg["network"]["mac"] == "auto"
        assert cfg["cloud_init"]["ssh_port"] == 22

    @pytest.mark.parametrize("port", [1, 22, 2222, 65535])
    def test_ssh_port(self, port):
        raw = _minimal_raw()
        raw["cloud_init"]["ssh_port"] = port
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["cloud_init"]["ssh_port"] == port
        assert kvm_vm.normalize_definition(cfg)["cloud_init"]["ssh_port"] == port
        assert kvm_vm.normalize_definition(raw, for_clone=True)["cloud_init"]["ssh_port"] == port

    @pytest.mark.parametrize("port", [0, -1, 65536, True, False, None, "2222", "bad", 22.5, [], {}])
    def test_invalid_ssh_port(self, port):
        raw = _minimal_raw()
        raw["cloud_init"]["ssh_port"] = port
        with pytest.raises(kvm_vm.KVMError, match="cloud_init.ssh_port must be an integer between 1 and 65535"):
            kvm_vm.normalize_definition(raw)

    def test_default_bridge_env_var(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_BRIDGE", "custombr0")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["network"]["bridge"] == "custombr0"

    def test_env_override_bridge_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_BRIDGE", "overridebr0")
        raw = _minimal_raw(network={"mode": "bridge", "bridge": "origbr99", "ipv4": {"method": "dhcp"}})
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["bridge"] == "overridebr0"

    def test_env_override_bridge_switches_nat_mode(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_BRIDGE", "overridebr0")
        raw = _minimal_raw(network={"mode": "nat", "libvirt_network": "default", "ipv4": {"method": "dhcp"}})
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["mode"] == "bridge"
        assert cfg["network"]["bridge"] == "overridebr0"
        assert "libvirt_network" not in cfg["network"]

    def test_env_override_bridge_empty_raises(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_BRIDGE", "  ")
        with pytest.raises(kvm_vm.KVMError, match="network.bridge must be a non-empty bridge"):
            kvm_vm.normalize_definition(_minimal_raw())

    def test_invalid_name(self):
        with pytest.raises(kvm_vm.KVMError, match="vm.name"):
            kvm_vm.normalize_definition({"version": 1, "vm": {"name": "-bad"}})

    def test_unsupported_version(self):
        raw = _minimal_raw()
        raw["version"] = 99
        with pytest.raises(kvm_vm.KVMError, match="Unsupported definition version"):
            kvm_vm.normalize_definition(raw)

    def test_nat_mode(self):
        raw = _minimal_raw(network={"mode": "nat", "libvirt_network": "mynet", "ipv4": {"method": "dhcp"}})
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["mode"] == "nat"
        assert cfg["network"]["libvirt_network"] == "mynet"
        assert "bridge" not in cfg["network"]

    def test_bridge_mode_explicit(self):
        raw = _minimal_raw(network={"mode": "bridge", "bridge": "br99", "ipv4": {"method": "dhcp"}})
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["mode"] == "bridge"
        assert cfg["network"]["bridge"] == "br99"
        assert "libvirt_network" not in cfg["network"]

    def test_invalid_network_mode(self):
        raw = _minimal_raw(network={"mode": "routed", "ipv4": {"method": "dhcp"}})
        with pytest.raises(kvm_vm.KVMError, match="bridge or nat"):
            kvm_vm.normalize_definition(raw)

    def test_static_ipv4(self):
        raw = _minimal_raw(network={
            "ipv4": {"method": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1", "dns": ["1.1.1.1"]},
        })
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["ipv4"]["method"] == "static"

    def test_static_ipv4_missing_gateway(self):
        raw = _minimal_raw(network={"ipv4": {"method": "static", "address": "10.0.0.5/24"}})
        with pytest.raises(kvm_vm.KVMError, match="gateway"):
            kvm_vm.normalize_definition(raw)

    def test_static_ipv4_invalid_format_chains_cause(self):
        raw = _minimal_raw(network={"ipv4": {"method": "static", "address": "not-an-ip", "gateway": "10.0.0.1"}})
        with pytest.raises(kvm_vm.KVMError) as exc_info:
            kvm_vm.normalize_definition(raw)
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_clone_disk_gib_none(self):
        raw = _minimal_raw()
        raw["storage"] = {}
        cfg = kvm_vm.normalize_definition(raw, for_clone=True)
        assert cfg["storage"]["disk_gib"] is None

    def test_vcpus_out_of_range(self):
        raw = _minimal_raw()
        raw["vm"]["vcpus"] = 0
        with pytest.raises(kvm_vm.KVMError, match="vcpus"):
            kvm_vm.normalize_definition(raw)

    def test_env_override_name(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_NAME", "envvm01")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["vm"]["name"] == "envvm01"

    def test_env_override_vcpus(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_VCPUS", "8")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["vm"]["vcpus"] == 8

    def test_env_override_vcpus_invalid(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_VCPUS", "not-a-number")
        with pytest.raises(kvm_vm.KVMError, match="Invalid KVM_VM_VCPUS"):
            kvm_vm.normalize_definition(_minimal_raw())

    def test_env_override_memory(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_MEMORY", "8192")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["vm"]["memory_mib"] == 8192

    def test_env_override_memory_mib_alias(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_MEMORY_MIB", "4096")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["vm"]["memory_mib"] == 4096

    def test_env_override_memory_invalid(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_MEMORY", "128")
        with pytest.raises(kvm_vm.KVMError, match="memory_mib must be >= 256"):
            kvm_vm.normalize_definition(_minimal_raw())

    def test_env_override_disk(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_DISK", "100")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["storage"]["disk_gib"] == 100

    def test_env_override_disk_clone(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_DISK_GIB", "150")
        raw = _minimal_raw()
        raw["storage"] = {}
        cfg = kvm_vm.normalize_definition(raw, for_clone=True)
        assert cfg["storage"]["disk_gib"] == 150

    def test_env_override_disk_invalid(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_DISK", "0")
        with pytest.raises(kvm_vm.KVMError, match="disk_gib must be >= 1"):
            kvm_vm.normalize_definition(_minimal_raw())

    def test_env_override_os(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_OS", "rocky9")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["storage"]["image"]["distro"] == "rocky9"
        assert cfg["vm"]["os_variant"] == "rocky9"

    def test_env_override_os_unsupported(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_OS", "nonexistent-distro")
        with pytest.raises(kvm_vm.KVMError, match="Unsupported KVM_VM_OS"):
            kvm_vm.normalize_definition(_minimal_raw())

    def test_env_override_ipv4_dhcp(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_IPV4", "dhcp")
        raw = _minimal_raw(network={
            "ipv4": {"method": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1"}
        })
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["network"]["ipv4"]["method"] == "dhcp"
        assert "address" not in cfg["network"]["ipv4"]

    def test_env_override_ipv4_static(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_IPV4", "192.168.10.77/24")
        monkeypatch.setenv("KVM_VM_GATEWAY", "192.168.10.1")
        monkeypatch.setenv("KVM_VM_DNS", "1.1.1.1,8.8.8.8")
        cfg = kvm_vm.normalize_definition(_minimal_raw())
        assert cfg["network"]["ipv4"]["method"] == "static"
        assert cfg["network"]["ipv4"]["address"] == "192.168.10.77/24"
        assert cfg["network"]["ipv4"]["gateway"] == "192.168.10.1"
        assert cfg["network"]["ipv4"]["dns"] == ["1.1.1.1", "8.8.8.8"]

    def test_env_override_ipv4_invalid(self, monkeypatch):
        monkeypatch.setenv("KVM_VM_IPV4", "invalid-ip-string")
        with pytest.raises(kvm_vm.KVMError, match="KVM_VM_IPV4 must be 'dhcp', 'disabled', or an IP interface with CIDR"):
            kvm_vm.normalize_definition(_minimal_raw())


# --- resolve_ssh_keys() & resolve_file_path() ---

class TestResolveFilePath:
    def test_absolute_path(self):
        p = kvm_vm.resolve_file_path("/etc/ssh/id.pub")
        assert p == Path("/etc/ssh/id.pub")

    def test_relative_path_with_base(self, tmp_path):
        p = kvm_vm.resolve_file_path("keys/id.pub", tmp_path)
        assert p == tmp_path / "keys" / "id.pub"

    def test_tilde_without_sudo(self, monkeypatch):
        monkeypatch.delenv("SUDO_USER", raising=False)
        p = kvm_vm.resolve_file_path("~/.ssh/id_ed25519.pub")
        assert p == Path("~/.ssh/id_ed25519.pub").expanduser()

    def test_tilde_with_sudo_user_pwd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUDO_USER", "mockuser")
        fake_home = tmp_path / "home" / "mockuser"
        fake_home.mkdir(parents=True)

        mock_pwnam = MagicMock()
        mock_pwnam.pw_dir = str(fake_home)
        mock_pwd = MagicMock()
        mock_pwd.getpwnam.return_value = mock_pwnam

        monkeypatch.setattr(kvm_vm, "pwd", mock_pwd)
        p = kvm_vm.resolve_file_path("~/.ssh/id_ed25519.pub")
        assert p == fake_home / ".ssh" / "id_ed25519.pub"

    def test_tilde_with_sudo_user_pwd_missing_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUDO_USER", "mockuser")
        fake_home = tmp_path / "mockuser"
        fake_home.mkdir()
        monkeypatch.setattr(kvm_vm, "pwd", None)

        with patch("os.path.expanduser", return_value=str(fake_home)):
            p = kvm_vm.resolve_file_path("~/.ssh/id_ed25519.pub")
            assert p == fake_home / ".ssh" / "id_ed25519.pub"

    def test_explicit_user_tilde_ignores_sudo(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUDO_USER", "alice")
        expected = tmp_path / "bob" / ".ssh" / "id.pub"
        with patch.object(Path, "expanduser", autospec=True, return_value=expected) as expanduser:
            p = kvm_vm.resolve_file_path("~bob/.ssh/id.pub")
        assert p == expected
        expanduser.assert_called_once_with(Path("~bob/.ssh/id.pub"))


class TestResolveSshKeys:
    def test_literal_key(self, tmp_path):
        keys = kvm_vm.resolve_ssh_keys(["ssh-ed25519 AAAA... test"], tmp_path)
        assert keys == ["ssh-ed25519 AAAA... test"]

    def test_file_key(self, tmp_path):
        pub = tmp_path / "id.pub"
        pub.write_text("ssh-ed25519 AAAA... fromfile\n# comment\n\n", encoding="utf-8")
        keys = kvm_vm.resolve_ssh_keys([f"file:{pub}"], tmp_path)
        assert keys == ["ssh-ed25519 AAAA... fromfile"]

    def test_tilde_ssh_key_sudo_user(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SUDO_USER", "mockuser")
        fake_home = tmp_path / "home" / "mockuser"
        ssh_dir = fake_home / ".ssh"
        ssh_dir.mkdir(parents=True)
        key_file = ssh_dir / "id_ed25519.pub"
        key_file.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... mock@test\n", encoding="utf-8")

        mock_pwnam = MagicMock()
        mock_pwnam.pw_dir = str(fake_home)
        mock_pwd = MagicMock()
        mock_pwd.getpwnam.return_value = mock_pwnam
        monkeypatch.setattr(kvm_vm, "pwd", mock_pwd)

        keys = kvm_vm.resolve_ssh_keys(["file:~/.ssh/id_ed25519.pub"], tmp_path)
        assert keys == ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... mock@test"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(kvm_vm.KVMError, match="not found"):
            kvm_vm.resolve_ssh_keys(["file:/nonexistent/key.pub"], tmp_path)

    def test_permission_error(self, tmp_path, monkeypatch):
        pub = tmp_path / "id.pub"
        pub.write_text("ssh-ed25519 AAAA... fromfile\n", encoding="utf-8")

        def mock_read_text(*args, **kwargs):
            raise PermissionError("Access denied")

        monkeypatch.setattr(Path, "read_text", mock_read_text)
        with pytest.raises(kvm_vm.KVMError, match="Permission denied"):
            kvm_vm.resolve_ssh_keys([f"file:{pub}"], tmp_path)

    def test_non_ssh_key_rejected(self, tmp_path):
        with pytest.raises(kvm_vm.KVMError, match="does not look like"):
            kvm_vm.resolve_ssh_keys(["not-a-real-key"], tmp_path)

    def test_relative_file(self, tmp_path):
        pub = tmp_path / "keys" / "id.pub"
        pub.parent.mkdir()
        pub.write_text("ssh-rsa AAAA... reltest\n", encoding="utf-8")
        keys = kvm_vm.resolve_ssh_keys(["file:keys/id.pub"], tmp_path)
        assert keys == ["ssh-rsa AAAA... reltest"]



# --- download() ---

class TestDownload:
    def test_download_success(self, tmp_path):
        fake_content = b"cloud-image-bytes"
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": str(len(fake_content))}
        mock_resp.read.side_effect = [fake_content, b""]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        dst = tmp_path / "base.qcow2"
        with patch("kvm_vm.urllib.request.urlopen", return_value=mock_resp):
            kvm_vm.download("https://example.com/base.qcow2", dst, retries=1)

        assert dst.exists()
        assert dst.read_bytes() == fake_content

    def test_download_retry_success(self, tmp_path):
        fake_content = b"cloud-image-bytes"
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": str(len(fake_content))}
        mock_resp.read.side_effect = [fake_content, b""]
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        dst = tmp_path / "base.qcow2"
        # First attempt raises URLError, second succeeds
        with patch("kvm_vm.urllib.request.urlopen", side_effect=[urllib.error.URLError("connection reset"), mock_resp]):
            kvm_vm.download("https://example.com/base.qcow2", dst, retries=2)

        assert dst.exists()
        assert dst.read_bytes() == fake_content

    def test_download_exhaustion_raises(self, tmp_path):
        dst = tmp_path / "base.qcow2"
        with patch("kvm_vm.urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            with pytest.raises(kvm_vm.KVMError, match="Download failed after 2 attempt"):
                kvm_vm.download("https://example.com/base.qcow2", dst, retries=2)


# --- build_parser() ---

class TestParser:
    def test_dry_run_flags(self):
        parser = kvm_vm.build_parser()

        args_create = parser.parse_args(["create", "myvm.yaml", "--dry-run"])
        assert args_create.dry_run is True

        args_clone = parser.parse_args(["clone", "src01", "myvm.yaml", "--dry-run"])
        assert args_clone.dry_run is True

        args_create_default = parser.parse_args(["create", "myvm.yaml"])
        assert args_create_default.dry_run is False

        args_reinstall = parser.parse_args(["reinstall", "myvm", "--dry-run"])
        assert args_reinstall.dry_run is True
        assert args_reinstall.name == "myvm"

        args_reinstall_opts = parser.parse_args([
            "reinstall", "myvm", "--os", "debian13", "--os-variant", "debian13",
            "--refresh-image", "--no-start", "--force", "--yes"
        ])
        assert args_reinstall_opts.os == "debian13"
        assert args_reinstall_opts.os_variant == "debian13"
        assert args_reinstall_opts.refresh_image is True
        assert args_reinstall_opts.no_start is True
        assert args_reinstall_opts.force is True
        assert args_reinstall_opts.yes is True

    def test_reinstall_mutually_exclusive_os(self):
        parser = kvm_vm.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["reinstall", "myvm", "--os", "debian13", "--image-url", "https://example.com/img.qcow2"])

    def test_version(self):
        assert kvm_vm.VERSION == "1.3.0"


# --- sysprep compatibility verification ---

class TestSysprepCompatibility:
    def test_selected_sysprep_ops_missing_critical(self):
        with patch("kvm_vm.supported_sysprep_ops", return_value=["random-seed"]):
            with pytest.raises(kvm_vm.KVMError, match="missing critical operation"):
                kvm_vm.selected_sysprep_ops()

    def test_selected_sysprep_ops_filters(self):
        with patch("kvm_vm.supported_sysprep_ops", return_value=["machine-id", "ssh-hostkeys", "extra-unknown"]):
            ops = kvm_vm.selected_sysprep_ops()
            assert ops == ["machine-id", "ssh-hostkeys"]

    def test_verify_sysprep_compatibility_success(self, tmp_path):
        fake_disk = tmp_path / "test.qcow2"
        fake_disk.touch()
        with patch("kvm_vm.supported_sysprep_ops", return_value=["machine-id", "ssh-hostkeys"]), \
             patch("kvm_vm.run") as mock_run:
            kvm_vm.verify_sysprep_compatibility(fake_disk)
            mock_run.assert_called_once()
            args, kwargs = mock_run.call_args
            cmd = args[0]
            assert cmd[0] == "virt-sysprep"
            assert "--dry-run" in cmd
            assert str(fake_disk) in cmd
            assert kwargs.get("capture") is True

    def test_verify_sysprep_compatibility_failure_raises(self, tmp_path):
        fake_disk = tmp_path / "test.qcow2"
        fake_disk.touch()
        exc = subprocess.CalledProcessError(1, ["virt-sysprep"], stderr="no operating system found")
        with patch("kvm_vm.supported_sysprep_ops", return_value=["machine-id", "ssh-hostkeys"]), \
             patch("kvm_vm.run", side_effect=exc):
            with pytest.raises(kvm_vm.KVMError, match="not compatible with virt-sysprep: no operating system found"):
                kvm_vm.verify_sysprep_compatibility(fake_disk)


# --- cloud seed generator ---

class TestCloudSeed:
    def test_find_cloud_seed_generator_priority(self):
        with patch("kvm_vm.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd in ("cloud-localds", "genisoimage") else None
            assert kvm_vm.find_cloud_seed_generator() == "cloud-localds"

    def test_find_cloud_seed_generator_fallback_genisoimage(self):
        with patch("kvm_vm.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/" + cmd if cmd in ("genisoimage", "xorriso") else None
            assert kvm_vm.find_cloud_seed_generator() == "genisoimage"

    def test_find_cloud_seed_generator_fallback_xorriso(self):
        with patch("kvm_vm.shutil.which") as mock_which:
            mock_which.side_effect = lambda cmd: "/usr/bin/xorriso" if cmd == "xorriso" else None
            assert kvm_vm.find_cloud_seed_generator() == "xorriso"

    def test_find_cloud_seed_generator_none(self):
        with patch("kvm_vm.shutil.which", return_value=None):
            assert kvm_vm.find_cloud_seed_generator() is None

    def test_require_cloud_seed_generator_raises_when_missing(self):
        with patch("kvm_vm.shutil.which", return_value=None):
            with pytest.raises(kvm_vm.KVMError, match="Missing cloud-init ISO generator"):
                kvm_vm.require_cloud_seed_generator()

    def test_build_cloud_seed_cmd_cloud_localds(self, tmp_path):
        seed = tmp_path / "seed.img"
        ud = tmp_path / "user-data"
        md = tmp_path / "meta-data"
        nc = tmp_path / "network-config"
        cmd = kvm_vm.build_cloud_seed_cmd("cloud-localds", seed, ud, md, nc)
        assert cmd == ["cloud-localds", "--network-config", str(nc), str(seed), str(ud), str(md)]

    def test_build_cloud_seed_cmd_genisoimage(self, tmp_path):
        seed = tmp_path / "seed.img"
        ud = tmp_path / "user-data"
        md = tmp_path / "meta-data"
        nc = tmp_path / "network-config"
        cmd = kvm_vm.build_cloud_seed_cmd("genisoimage", seed, ud, md, nc)
        assert cmd[0] == "genisoimage"
        assert "-quiet" in cmd
        assert "-volid" in cmd and "cidata" in cmd
        assert "-graft-points" in cmd
        assert f"user-data={ud}" in cmd
        assert f"meta-data={md}" in cmd
        assert f"network-config={nc}" in cmd

    def test_build_cloud_seed_cmd_xorriso(self, tmp_path):
        seed = tmp_path / "seed.img"
        ud = tmp_path / "user-data"
        md = tmp_path / "meta-data"
        nc = tmp_path / "network-config"
        cmd = kvm_vm.build_cloud_seed_cmd("xorriso", seed, ud, md, nc)
        assert cmd[:3] == ["xorriso", "-as", "mkisofs"]
        assert "-quiet" in cmd
        assert "-volid" in cmd and "cidata" in cmd
        assert f"user-data={ud}" in cmd

    def test_build_cloud_seed_cmd_unsupported(self, tmp_path):
        seed = tmp_path / "seed.img"
        ud = tmp_path / "user-data"
        md = tmp_path / "meta-data"
        nc = tmp_path / "network-config"
        with pytest.raises(kvm_vm.KVMError, match="Unsupported cloud-init generator"):
            kvm_vm.build_cloud_seed_cmd("unknown-tool", seed, ud, md, nc)

    def test_create_cloud_seed_unlinks_existing_and_restores_selinux(self, tmp_path):
        seed = tmp_path / "seed.img"
        seed.touch()
        ud = tmp_path / "user-data"
        md = tmp_path / "meta-data"
        nc = tmp_path / "network-config"

        with patch("kvm_vm.require_cloud_seed_generator", return_value="genisoimage"), \
             patch("kvm_vm.run") as mock_run, \
             patch("kvm_vm.restore_selinux") as mock_selinux:
            kvm_vm.create_cloud_seed(seed, ud, md, nc)
            assert not seed.exists()
            mock_run.assert_called_once()
            mock_selinux.assert_called_once_with([seed])


class TestSshPortCommand:
    MODES = ["socket-active", "socket-enabled", "ssh", "sshd"]
    MAIN = "#Port 22\nPort 2200\nInclude sshd_config.d/*.conf\nPasswordAuthentication no\nMatch User admin\n    X11Forwarding no\n"
    SNIPPET = "  pOrT=2201\nPermitRootLogin no\n"
    OVERRIDE = "[Socket]\nListenStream=\nListenStream=0.0.0.0:2222\nListenStream=[::]:2222\nBindIPv6Only=ipv6-only\n"

    @staticmethod
    def make_guest(tmp_path, mode, main=MAIN, snippet=SNIPPET, valid_config=True, restart_fails=False):
        """Lay out a fake guest root with mocked systemctl and sshd that log calls.

        With restart_fails, restarting SSH fails while it is set to port 2222,
        as when SELinux or another service blocks the port.
        """
        fail = "yes" if restart_fails else "no"
        if not shutil.which("sh"):
            pytest.skip("POSIX shell required to execute guest provisioning commands")
        ssh_dir = tmp_path / "ssh"
        (ssh_dir / "sshd_config.d").mkdir(parents=True)
        (ssh_dir / "sshd_config").write_text(main, encoding="utf-8")
        (ssh_dir / "sshd_config.d" / "custom.conf").write_text(snippet, encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        systemctl = bin_dir / "systemctl"
        systemctl.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> calls
case "$*" in
    'is-active --quiet ssh.socket') [ '{mode}' = socket-active ] ;;
    'is-enabled --quiet ssh.socket') [ '{mode}' = socket-enabled ] ;;
    'show -p LoadState --value ssh.service')
        if [ '{mode}' = sshd ]; then echo not-found; else echo loaded; fi ;;
    'restart ssh.socket') [ '{fail}' = no ] || ! grep -qs ':2222' system/ssh.socket.d/zz-kvm-vm-port.conf ;;
    'restart '*) [ '{fail}' = no ] || ! grep -qs '^Port 2222' ssh/sshd_config ;;
    *) exit 0 ;;
esac
""", encoding="utf-8")
        sshd = bin_dir / "sshd"
        sshd.write_text(f"#!/bin/sh\nprintf '%s\\n' \"sshd $*\" >> calls\n[ -d ./run/sshd ] || exit 2\nexit {0 if valid_config else 1}\n", encoding="utf-8")
        systemctl.chmod(0o755)
        sshd.chmod(0o755)

    @staticmethod
    def restart_calls(mode):
        if mode.startswith("socket"):
            return ["daemon-reload", "restart ssh.socket", "try-restart ssh.service"]
        return [f"restart {mode}.service"]

    @staticmethod
    def run_command(tmp_path, port):
        command = kvm_vm.ssh_port_command(port)
        command = command.replace("/etc/ssh/", "./ssh/").replace("/etc/systemd/system/", "./system/").replace("/usr/sbin/sshd", "./bin/sshd").replace("/run/sshd", "./run/sshd")
        script = tmp_path / "provision.sh"
        # Write bytes so Windows keeps LF line endings, which dash requires.
        script.write_bytes(('PATH="$PWD/bin:$PATH"\nexport PATH\n' + command).encode("utf-8"))
        return subprocess.run([shutil.which("sh"), script.name], text=True, cwd=tmp_path, capture_output=True)

    @staticmethod
    def calls(tmp_path):
        log = tmp_path / "calls"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    @staticmethod
    def actions(tmp_path):
        """Calls that validate or change the running listener, without state queries."""
        return [c for c in TestSshPortCommand.calls(tmp_path) if c == "sshd -t" or c == "daemon-reload" or "restart" in c]

    @pytest.mark.parametrize("mode", MODES)
    def test_replaces_old_ports_and_restarts_listener(self, tmp_path, mode):
        self.make_guest(tmp_path, mode)
        # Running twice verifies that a cloned/reprovisioned guest remains idempotent.
        for _ in range(2):
            assert self.run_command(tmp_path, 2222).returncode == 0
        assert (tmp_path / "ssh/sshd_config").read_text(encoding="utf-8") == "Port 2222\n" + self.MAIN.replace("Port 2200\n", "")
        assert (tmp_path / "ssh/sshd_config.d/custom.conf").read_text(encoding="utf-8") == "PermitRootLogin no\n"
        assert self.actions(tmp_path) == (["sshd -t"] + self.restart_calls(mode)) * 2
        override = tmp_path / "system/ssh.socket.d/zz-kvm-vm-port.conf"
        if mode.startswith("socket"):
            assert override.read_text(encoding="utf-8") == self.OVERRIDE
        else:
            assert not override.exists()

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("failure", ["invalid-config", "restart"])
    @pytest.mark.parametrize("had_dropin", [False, True])
    def test_failure_restores_previous_config(self, tmp_path, mode, failure, had_dropin):
        self.make_guest(tmp_path, mode, valid_config=failure != "invalid-config", restart_fails=failure == "restart")
        dropin = tmp_path / "system/ssh.socket.d/zz-kvm-vm-port.conf"
        old_dropin = self.OVERRIDE.replace("2222", "2200")
        if had_dropin:
            # A clone of a guest that kvm-vm already moved to another port.
            dropin.parent.mkdir(parents=True)
            dropin.write_text(old_dropin, encoding="utf-8")

        result = self.run_command(tmp_path, 2222)

        assert result.returncode != 0
        assert "kvm-vm: could not move SSH to port 2222; restoring the previous SSH configuration" in result.stderr
        assert (tmp_path / "ssh/sshd_config").read_text(encoding="utf-8") == self.MAIN
        assert (tmp_path / "ssh/sshd_config.d/custom.conf").read_text(encoding="utf-8") == self.SNIPPET
        if had_dropin:
            assert dropin.read_text(encoding="utf-8") == old_dropin
        else:
            assert not dropin.exists()
        restart = self.restart_calls(mode)
        if failure == "invalid-config":
            # The listener never changed, but restarting it again is harmless.
            assert self.actions(tmp_path) == ["sshd -t"] + restart
        else:
            # The new port fails to start; the restored files start the old listener.
            failed = restart[:2] if mode.startswith("socket") else restart
            assert self.actions(tmp_path) == ["sshd -t"] + failed + restart

    @pytest.mark.parametrize("mode", MODES)
    def test_default_port_removes_kvm_vm_settings(self, tmp_path, mode):
        """A clone that targets port 22 drops the source guest's custom port."""
        self.make_guest(tmp_path, mode)
        assert self.run_command(tmp_path, 2222).returncode == 0
        (tmp_path / "calls").unlink()
        assert self.run_command(tmp_path, 22).returncode == 0
        assert (tmp_path / "ssh/sshd_config").read_text(encoding="utf-8") == self.MAIN.replace("Port 2200\n", "")
        assert (tmp_path / "ssh/sshd_config.d/custom.conf").read_text(encoding="utf-8") == "PermitRootLogin no\n"
        assert not (tmp_path / "system/ssh.socket.d/zz-kvm-vm-port.conf").exists()
        assert self.actions(tmp_path) == ["sshd -t"] + self.restart_calls(mode)

    @pytest.mark.parametrize("mode", MODES)
    def test_failed_reset_keeps_custom_port(self, tmp_path, mode):
        self.make_guest(tmp_path, mode)
        assert self.run_command(tmp_path, 2222).returncode == 0
        before = {p: p.read_text(encoding="utf-8") for p in tmp_path.glob("s*/**/*") if p.is_file()}
        (tmp_path / "bin/sshd").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        (tmp_path / "calls").unlink()

        result = self.run_command(tmp_path, 22)

        assert result.returncode != 0
        assert "could not move SSH to port 22" in result.stderr
        assert {p: p.read_text(encoding="utf-8") for p in tmp_path.glob("s*/**/*") if p.is_file()} == before
        assert self.actions(tmp_path) == self.restart_calls(mode)

    @pytest.mark.parametrize("mode", MODES)
    def test_default_port_leaves_unchanged_guest_untouched(self, tmp_path, mode):
        main = "#Port 22\nPasswordAuthentication no\n"
        snippet = "PermitRootLogin no\n"
        self.make_guest(tmp_path, mode, main=main, snippet=snippet)
        assert self.run_command(tmp_path, 22).returncode == 0
        assert (tmp_path / "ssh/sshd_config").read_text(encoding="utf-8") == main
        assert (tmp_path / "ssh/sshd_config.d/custom.conf").read_text(encoding="utf-8") == snippet
        assert not (tmp_path / "system").exists()
        assert self.calls(tmp_path) == []


class TestMakeCloudFiles:
    @pytest.mark.parametrize("user", ["admin", "sysadmin"])
    def test_user_has_explicit_primary_group(self, tmp_path, monkeypatch, user):
        monkeypatch.setattr(kvm_vm, "CLOUD_DIR", tmp_path / "cloud-init")
        raw = _minimal_raw()
        raw["cloud_init"]["user"] = user
        cfg = kvm_vm.normalize_definition(raw)
        with patch("kvm_vm.create_cloud_seed"):
            ud, _, _, _ = kvm_vm.make_cloud_files(cfg, tmp_path)
        data = kvm_vm.yaml.safe_load(ud.read_text(encoding="utf-8"))
        assert data["users"][0]["primary_group"] == user
        assert user in data["users"][0]["groups"]

    @staticmethod
    def make_user_data(tmp_path, monkeypatch, port, **kwargs):
        monkeypatch.setattr(kvm_vm, "CLOUD_DIR", tmp_path / "cloud-init")
        raw = _minimal_raw()
        raw["cloud_init"]["runcmd"] = [["echo", "user command"]]
        if port is not None:
            raw["cloud_init"]["ssh_port"] = port
        cfg = kvm_vm.normalize_definition(raw)
        if port is None:
            # Older stored definitions do not have the new field.
            cfg["cloud_init"].pop("ssh_port")
        with patch("kvm_vm.create_cloud_seed"):
            ud, _, _, _ = kvm_vm.make_cloud_files(cfg, tmp_path, **kwargs)
        assert cfg["cloud_init"]["runcmd"] == [["echo", "user command"]]
        return kvm_vm.yaml.safe_load(ud.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("reset_ssh_port", [False, True])
    def test_custom_ssh_port_provisioning(self, tmp_path, monkeypatch, reset_ssh_port):
        data = self.make_user_data(tmp_path, monkeypatch, 2222, reset_ssh_port=reset_ssh_port)
        assert data["ssh_pwauth"] is False
        assert data["disable_root"] is True
        assert data["runcmd"][0] == ["echo", "user command"]
        assert data["runcmd"][1] == ["systemctl", "enable", "--now", "qemu-guest-agent"]
        command = data["runcmd"][-1]
        assert command[:2] == ["sh", "-c"]
        assert command[2] == kvm_vm.ssh_port_command(2222)
        assert "port=2222\n" in command[2]
        assert "ListenStream=0.0.0.0:%s" in command[2]
        assert "ListenStream=[::]:%s" in command[2]
        assert "BindIPv6Only=ipv6-only" in command[2]
        assert "ListenStream=\\nListenStream=" in command[2]
        # TestSshPortCommand executes the command to check call order.
        assert "systemctl restart ssh.socket" in command[2]
        assert "systemctl restart ssh.service" in command[2]
        assert "systemctl restart sshd.service" in command[2]

    @pytest.mark.parametrize("port", [None, 22])
    def test_default_ssh_port_leaves_fresh_image_alone(self, tmp_path, monkeypatch, port):
        data = self.make_user_data(tmp_path, monkeypatch, port)
        assert data["runcmd"] == [["echo", "user command"], ["systemctl", "enable", "--now", "qemu-guest-agent"]]

    @pytest.mark.parametrize("port", [None, 22])
    def test_default_ssh_port_resets_cloned_disk(self, tmp_path, monkeypatch, port):
        data = self.make_user_data(tmp_path, monkeypatch, port, reset_ssh_port=True)
        assert data["runcmd"][:2] == [["echo", "user command"], ["systemctl", "enable", "--now", "qemu-guest-agent"]]
        assert data["runcmd"][2] == ["sh", "-c", kvm_vm.ssh_port_command(22)]
        assert len(data["runcmd"]) == 3

    def test_clone_resets_ssh_port(self, tmp_path):
        def_file = tmp_path / "clone01.yaml"
        kvm_vm.yaml_dump(def_file, _minimal_raw(vm={"name": "clone01"}))
        src_disk = tmp_path / "src01.qcow2"
        src_disk.touch()
        args = MagicMock()
        args.source = "src01"
        args.definition = str(def_file)
        args.dry_run = False
        args.no_start = False

        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", side_effect=lambda name: name == "src01"), \
             patch("kvm_vm.domain_state", return_value="shut off"), \
             patch("kvm_vm.load_state", side_effect=lambda name: {"disk": str(src_disk)} if name == "src01" else None), \
             patch("kvm_vm.validate_network_attachment"), \
             patch("kvm_vm.existing_macs", return_value=set()), \
             patch("kvm_vm.verify_sysprep_compatibility"), \
             patch("kvm_vm.make_disk_from_clone"), \
             patch("kvm_vm.restore_selinux"), \
             patch("kvm_vm.sysprep_clone_disk"), \
             patch("kvm_vm.make_cloud_files", return_value=(None, None, None, "iid-123")) as mock_cloud, \
             patch("kvm_vm.define_vm"), \
             patch("kvm_vm.subprocess.run", side_effect=lambda cmd, **kw: lifecycle_probe(cmd, src_disk, **kw)), \
             patch("kvm_vm.save_state"):
            assert kvm_vm.cmd_clone(args) == 0

        # The copied disk may still carry the source guest's SSH port.
        assert mock_cloud.call_args.kwargs == {"reset_ssh_port": True}

    def test_static_ipv4_uses_cidr_default_route(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kvm_vm, "CLOUD_DIR", tmp_path / "cloud-init")
        raw = _minimal_raw(
            network={
                "mode": "bridge",
                "bridge": "br0",
                "mac": "52:54:00:11:22:33",
                "ipv4": {
                    "method": "static",
                    "address": "192.168.10.50/24",
                    "gateway": "192.168.10.1",
                    "dns": ["8.8.8.8"],
                },
            }
        )
        cfg = kvm_vm.normalize_definition(raw)
        with patch("kvm_vm.create_cloud_seed"):
            ud, md, nc, iid = kvm_vm.make_cloud_files(cfg, tmp_path)

        import yaml
        net_data = yaml.safe_load(nc.read_text(encoding="utf-8"))
        assert net_data["version"] == 2
        eth0 = net_data["ethernets"]["eth0"]
        assert eth0["dhcp4"] is False
        assert eth0["addresses"] == ["192.168.10.50/24"]
        # Must be 0.0.0.0/0 rather than 'default' so cloud-init's NetworkState parser accepts it
        assert eth0["routes"] == [{"to": "0.0.0.0/0", "via": "192.168.10.1"}]
        assert eth0["nameservers"]["addresses"] == ["8.8.8.8"]

    def test_static_ipv6_uses_cidr_default_route(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kvm_vm, "CLOUD_DIR", tmp_path / "cloud-init")
        raw = _minimal_raw(
            network={
                "mode": "bridge",
                "bridge": "br0",
                "mac": "52:54:00:11:22:33",
                "ipv4": {"method": "disabled"},
                "ipv6": {
                    "method": "static",
                    "address": "2001:db8::10/64",
                    "gateway": "2001:db8::1",
                },
            }
        )
        cfg = kvm_vm.normalize_definition(raw)
        with patch("kvm_vm.create_cloud_seed"):
            ud, md, nc, iid = kvm_vm.make_cloud_files(cfg, tmp_path)

        import yaml
        net_data = yaml.safe_load(nc.read_text(encoding="utf-8"))
        eth0 = net_data["ethernets"]["eth0"]
        assert eth0["dhcp4"] is False
        assert eth0["dhcp6"] is False
        assert eth0["addresses"] == ["2001:db8::10/64"]
        assert eth0["routes"] == [{"to": "::/0", "via": "2001:db8::1"}]

    def test_dhcp_network_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kvm_vm, "CLOUD_DIR", tmp_path / "cloud-init")
        raw = _minimal_raw(
            network={
                "mode": "nat",
                "libvirt_network": "default",
                "mac": "52:54:00:11:22:33",
                "ipv4": {"method": "dhcp"},
            }
        )
        cfg = kvm_vm.normalize_definition(raw)
        with patch("kvm_vm.create_cloud_seed"):
            ud, md, nc, iid = kvm_vm.make_cloud_files(cfg, tmp_path)

        import yaml
        net_data = yaml.safe_load(nc.read_text(encoding="utf-8"))
        eth0 = net_data["ethernets"]["eth0"]
        assert eth0["dhcp4"] is True
        assert "routes" not in eth0


# --- reinstall tests ---


class TestApplyOsOverride:
    def test_distro_override(self):
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, os_arg="debian13")
        assert raw["storage"]["image"]["distro"] == "debian13"
        assert raw["storage"]["image"]["url"] is None
        assert raw["storage"]["image"]["path"] is None
        assert raw["vm"]["os_variant"] == "debian13"

    def test_url_override(self):
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, os_arg="https://example.com/noble.img")
        assert raw["storage"]["image"]["url"] == "https://example.com/noble.img"
        assert raw["storage"]["image"]["distro"] is None
        assert raw["storage"]["image"]["path"] is None

    def test_local_file_override(self, tmp_path):
        img = tmp_path / "base.qcow2"
        img.touch()
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, os_arg=str(img))
        assert raw["storage"]["image"]["path"] == str(img.resolve())
        assert raw["storage"]["image"]["distro"] is None

    def test_unsupported_distro_raises(self):
        raw = _minimal_raw()
        with pytest.raises(kvm_vm.KVMError, match="Unsupported OS or distro"):
            kvm_vm.apply_os_override(raw, os_arg="unknown-distro")

    def test_missing_image_file_raises(self):
        raw = _minimal_raw()
        with pytest.raises(kvm_vm.KVMError, match="Base image not found"):
            kvm_vm.apply_os_override(raw, os_arg="/nonexistent/dir/custom.qcow2")

    def test_image_url_flag(self):
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, image_url="https://example.com/cloud.qcow2")
        assert raw["storage"]["image"]["url"] == "https://example.com/cloud.qcow2"
        assert raw["storage"]["image"]["distro"] is None

    def test_image_path_flag_missing_raises(self):
        raw = _minimal_raw()
        with pytest.raises(kvm_vm.KVMError, match="Base image not found"):
            kvm_vm.apply_os_override(raw, image_path="/nonexistent/cloud.qcow2")

    def test_os_variant_flag(self):
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, os_variant="centos-stream9")
        assert raw["vm"]["os_variant"] == "centos-stream9"

    def test_no_override_leaves_original(self):
        raw = _minimal_raw()
        orig_distro = raw["storage"]["image"]["distro"]
        kvm_vm.apply_os_override(raw)
        assert raw["storage"]["image"]["distro"] == orig_distro


class TestConfirmReinstall:
    def test_confirm_yes(self):
        kvm_vm.confirm_reinstall("web01", yes=True)

    def test_confirm_non_interactive_raises(self):
        with patch("sys.stdin.isatty", return_value=False):
            with pytest.raises(kvm_vm.KVMError, match="Refusing destructive reinstall without --yes in non-interactive mode"):
                kvm_vm.confirm_reinstall("web01", yes=False)

    def test_confirm_interactive_success(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="web01"):
            kvm_vm.confirm_reinstall("web01", yes=False)

    def test_confirm_interactive_cancelled(self):
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value="no"):
            with pytest.raises(kvm_vm.KVMError, match="Reinstall cancelled"):
                kvm_vm.confirm_reinstall("web01", yes=False)


@pytest.fixture
def reinstall_machine(tmp_path, monkeypatch):
    """Real reinstall/filesystem, with only host programs simulated."""
    for key in ("BASE_DIR", "VM_DIR", "STATE_DIR", "CLOUD_DIR", "DEF_DIR"):
        directory = tmp_path / key.lower()
        directory.mkdir()
        monkeypatch.setattr(kvm_vm, key, directory)
    base = kvm_vm.BASE_DIR / "base.qcow2"
    base.write_bytes(b"base image")
    disk = kvm_vm.VM_DIR / "test01.qcow2"
    disk.write_bytes(b"original guest data")
    cfg = kvm_vm.normalize_definition(_minimal_raw(
        storage={"image": {"path": str(base)}},
        network={"mode": "nat", "libvirt_network": "default",
                 "mac": "52:54:00:99:88:77", "ipv4": {"method": "dhcp"}},
    ))
    cdir = kvm_vm.CLOUD_DIR / "test01"
    cdir.mkdir()
    for filename in ("seed.img", "user-data", "meta-data", "network-config"):
        (cdir / filename).write_bytes(b"original " + filename.encode())
    definition = kvm_vm.DEF_DIR / "test01.yaml"
    kvm_vm.save_state(cfg, disk, {}, "original-instance", definition)
    clone_definition = tmp_path / "clone.yaml"
    clone_cfg = dict(cfg, vm=dict(cfg["vm"], name="clone01"))
    kvm_vm.yaml_dump(clone_definition, clone_cfg)
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    machine = {"defined": True, "failure": "checkpoint refusal", "disk": disk,
               "originals": originals, "root": tmp_path}
    monkeypatch.setattr(kvm_vm.shutil, "which", lambda name: None if name == "restorecon" else name)

    def external(cmd, **kwargs):
        code, stdout, stderr = 0, "", ""
        if cmd[0] == "virsh":
            action = cmd[3]
            if action == "dominfo":
                exists = machine["defined"] if cmd[4] == "test01" else machine.get("target_defined", False)
                code = 0 if exists else 1
            elif action == "domstate":
                stdout = machine.get("power", "shut off")
            elif action == "domjobinfo":
                stdout = "Job type: " + machine.get("job", "None")
            elif action == "blockjob":
                stdout = machine.get("blockjob", "")
            elif action == "list":
                stdout = "test01\nother" if machine.get("other_domain_disk") else "test01"
            elif action == "net-info":
                stdout = "Active: yes"
            elif action == "net-dumpxml":
                stdout = '<network><forward mode="nat"/></network>'
            elif action == "checkpoint-list":
                stdout = machine.get("checkpoints", "")
            elif action == "snapshot-list":
                stdout = machine.get("snapshots", "")
            elif action == "dumpxml":
                root = kvm_vm.ET.Element("domain")
                devices = kvm_vm.ET.SubElement(root, "devices")
                item = kvm_vm.ET.SubElement(devices, "disk", type="file", device="disk")
                kvm_vm.ET.SubElement(item, "driver", type="qcow2")
                source_disk = machine["other_domain_disk"] if cmd[4] == "other" else machine.get("active_disk", disk)
                kvm_vm.ET.SubElement(item, "source", file=str(source_disk))
                kvm_vm.ET.SubElement(item, "target", dev="vda")
                stdout = kvm_vm.ET.tostring(root, encoding="unicode")
            elif action == "undefine":
                if machine["failure"]:
                    code, stderr = 1, machine["failure"]
                elif machine.get("nvram") and not any(flag in cmd for flag in ("--nvram", "--keep-nvram")):
                    code, stderr = 1, "NVRAM requires explicit handling"
                else:
                    if "--nvram" in cmd and machine.get("nvram"):
                        machine["nvram"].unlink()
                    machine["defined"] = False
            elif action == "autostart":
                pass
            else:
                raise AssertionError(cmd)
        elif cmd[:2] == ["qemu-img", "info"]:
            info = {"format": "qcow2", "virtual-size": 20 * 1024**3}
            if Path(cmd[-1]) == disk:
                info.update(machine.get("disk_info", {}))
            info.update(machine.get("other_images", {}).get(str(Path(cmd[-1])), {}))
            stdout = json.dumps(info)
        elif cmd[:2] == ["qemu-img", "convert"]:
            Path(cmd[-1]).write_bytes(disk.read_bytes() if Path(cmd[-2]) == disk else b"replacement guest data")
        elif cmd[0] == "virt-sysprep":
            if "--list-operations" in cmd:
                stdout = "machine-id\nssh-hostkeys"
        elif cmd[0] == "virt-customize":
            pass
        elif cmd[0] == "cloud-localds":
            Path(cmd[-3]).write_bytes(b"replacement seed")
        elif cmd[0] == "virt-install":
            if cmd[cmd.index("--name") + 1] == "test01":
                machine["defined"] = True
            else:
                machine["target_defined"] = True
            if not machine.get("install_succeeds"):
                code, stderr = 1, "domain defined but start failed"
        else:
            raise AssertionError(cmd)
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, cmd, stdout, stderr)
        return subprocess.CompletedProcess(cmd, code, stdout, stderr)

    monkeypatch.setattr(kvm_vm.subprocess, "run", external)
    machine["args"] = argparse.Namespace(name="test01", force=False, dry_run=False,
        yes=True, refresh_image=False, no_start=True, source="test01", definition=str(clone_definition))
    return machine


@pytest.mark.parametrize("reason", ["checkpoint refusal", "arbitrary undefine failure"])
def test_reinstall_undefine_failure_preserves_existing_data(reinstall_machine, reason):
    machine = reinstall_machine
    machine["failure"] = reason
    with pytest.raises(kvm_vm.KVMError, match=reason):
        kvm_vm.cmd_reinstall(machine["args"])
    assert machine["defined"]
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents
    assert set(p for p in machine["root"].rglob("*") if p.is_file()) == set(machine["originals"])


def test_reinstall_preserves_unowned_staging_files(reinstall_machine):
    machine = reinstall_machine
    disk = machine["disk"]
    stale = disk.with_name(disk.name + ".reinstall.tmp")
    nested = stale.with_suffix(stale.suffix + ".tmp")
    for path in (stale, nested):
        path.write_bytes(b"previous attempt: recover manually")
    with pytest.raises(kvm_vm.KVMError, match="checkpoint refusal"):
        kvm_vm.cmd_reinstall(machine["args"])
    for path in (stale, nested):
        assert path.read_bytes() == b"previous attempt: recover manually"


def test_reinstall_late_failure_preserves_replacement(reinstall_machine, caplog, capsys):
    machine = reinstall_machine
    machine["failure"] = None
    with pytest.raises(subprocess.CalledProcessError) as error:
        kvm_vm.cmd_reinstall(machine["args"])
    assert error.value.stderr == "domain defined but start failed"
    assert machine["defined"]
    assert machine["disk"].read_bytes() == b"replacement guest data"
    assert (kvm_vm.CLOUD_DIR / "test01" / "seed.img").read_bytes() == b"replacement seed"
    for path, contents in machine["originals"].items():
        if path.parent in (kvm_vm.STATE_DIR, kvm_vm.DEF_DIR):
            assert path.read_bytes() == contents
    assert "Reinstalled VM:" not in capsys.readouterr().out
    assert "manual recovery" in caplog.text
    assert "replacement" in caplog.text


def test_reinstall_cleanup_failure_keeps_original_error(reinstall_machine, monkeypatch, caplog):
    machine = reinstall_machine

    def denied(path, *args, **kwargs):
        raise PermissionError("staging cleanup denied")

    monkeypatch.setattr(kvm_vm.shutil, "rmtree", denied)
    with pytest.raises(kvm_vm.KVMError, match="checkpoint refusal"):
        kvm_vm.cmd_reinstall(machine["args"])
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents
    leftovers = list(machine["disk"].parent.glob(".test01-reinstall-*"))
    assert len(leftovers) == 1
    assert str(leftovers[0]) in caplog.text
    assert "staging cleanup denied" in caplog.text


def test_reinstall_success_through_command_boundary(reinstall_machine, capsys):
    machine = reinstall_machine
    machine.update(failure=None, install_succeeds=True)
    assert kvm_vm.cmd_reinstall(machine["args"]) == 0
    assert machine["defined"]
    assert machine["disk"].read_bytes() == b"replacement guest data"
    state = json.loads((kvm_vm.STATE_DIR / "test01.json").read_text())
    assert state["disk"] == str(machine["disk"])
    assert state["instance_id"] != "original-instance"
    assert Path(state["seed"]).read_bytes() == b"replacement seed"
    assert not list(machine["disk"].parent.glob(".test01-reinstall-*"))
    assert "Reinstalled VM: test01" in capsys.readouterr().out


@pytest.mark.parametrize("operation", ["reinstall", "clone", "delete"])
def test_lifecycle_refuses_backup_checkpoints_before_mutation(reinstall_machine, operation):
    machine = reinstall_machine
    machine["checkpoints"] = "backup-baseline"
    with pytest.raises(kvm_vm.KVMError, match="Backup checkpoints"):
        args = machine["args"]
        args.source = args.name
        args.keep_storage = True
        getattr(kvm_vm, "cmd_" + operation)(args)
    assert machine["defined"]
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


@pytest.mark.parametrize("operation", ["reinstall", "clone", "delete"])
def test_lifecycle_refuses_stale_disk_path(reinstall_machine, operation):
    machine = reinstall_machine
    active = machine["disk"].with_name("overlay.qcow2")
    active.write_bytes(b"current guest writes")
    machine["active_disk"] = active
    machine["args"].source = "test01"
    machine["args"].keep_storage = True
    with pytest.raises(kvm_vm.KVMError, match="disk path"):
        getattr(kvm_vm, "cmd_" + operation)(machine["args"])
    assert active.read_bytes() == b"current guest writes"
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


@pytest.mark.parametrize("info", [
    {"backing-filename": "base.qcow2"},
    {"snapshots": [{"name": "manual"}]},
    {"format-specific": {"data": {"bitmaps": [{"name": "backup"}]}}},
])
def test_reinstall_refuses_image_recovery_dependencies(reinstall_machine, info):
    machine = reinstall_machine
    machine["disk_info"] = info
    with pytest.raises(kvm_vm.KVMError, match="recovery data"):
        kvm_vm.cmd_reinstall(machine["args"])
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


@pytest.mark.parametrize("conflict", ["snapshots", "job", "blockjob"])
def test_reinstall_refuses_recovery_conflicts(reinstall_machine, conflict):
    machine = reinstall_machine
    machine[conflict] = "active-work"
    machine["power"] = "running"
    machine["args"].force = True
    with pytest.raises(kvm_vm.KVMError, match="recovery operation"):
        kvm_vm.cmd_reinstall(machine["args"])
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


def test_conflicting_lifecycle_work_preserves_lock_and_vm(reinstall_machine):
    machine = reinstall_machine
    lock = kvm_vm.STATE_DIR / ".lifecycle-lock"
    lock.mkdir()
    owner = lock / "owner"
    owner.write_text("another operator")
    with pytest.raises(kvm_vm.KVMError, match="lifecycle operation"):
        kvm_vm.cmd_reinstall(machine["args"])
    assert owner.read_text() == "another operator"
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


@pytest.mark.parametrize("filename", ["dependent.qcow2", "dependent.img", "dependent"])
@pytest.mark.parametrize("operation", ["delete", "reinstall"])
def test_lifecycle_preserves_disk_used_by_unattached_overlay(reinstall_machine, filename, operation):
    machine = reinstall_machine
    overlay = machine["disk"].with_name(filename)
    overlay.write_bytes(b"dependent writes")
    machine["other_images"] = {str(overlay): {"backing-filename": str(machine["disk"])}}
    machine["args"].keep_storage = False
    with pytest.raises(kvm_vm.KVMError, match="dependent disk"):
        getattr(kvm_vm, "cmd_" + operation)(machine["args"])
    assert machine["defined"]
    assert overlay.read_bytes() == b"dependent writes"
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


def test_keep_storage_preserves_nvram_and_managed_files(reinstall_machine):
    machine = reinstall_machine
    nvram = machine["root"] / "firmware.vars"
    nvram.write_bytes(b"firmware state")
    machine.update(failure=None, nvram=nvram)
    machine["args"].keep_storage = True
    assert kvm_vm.cmd_delete(machine["args"]) == 0
    assert not machine["defined"]
    assert nvram.read_bytes() == b"firmware state"
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


def test_clone_failure_keeps_disk_when_target_undefine_would_fail(reinstall_machine):
    machine = reinstall_machine
    with pytest.raises(subprocess.CalledProcessError):
        kvm_vm.cmd_clone(machine["args"])
    target = kvm_vm.VM_DIR / "clone01.qcow2"
    assert machine["target_defined"]
    assert target.read_bytes() == b"original guest data"
    assert (kvm_vm.CLOUD_DIR / "clone01" / "seed.img").is_file()
    for path, contents in machine["originals"].items():
        assert path.read_bytes() == contents


def test_clone_refuses_unowned_cloud_artifacts(reinstall_machine):
    machine = reinstall_machine
    cloud = kvm_vm.CLOUD_DIR / "clone01"
    cloud.mkdir()
    seed = cloud / "seed.img"
    seed.write_bytes(b"untracked recovery seed")
    with pytest.raises(kvm_vm.KVMError, match="Existing artifacts"):
        kvm_vm.cmd_clone(machine["args"])
    assert seed.read_bytes() == b"untracked recovery seed"
    assert not (kvm_vm.VM_DIR / "clone01.qcow2").exists()


def test_reinstall_uses_inspected_disk_when_manifest_is_absent(reinstall_machine):
    machine = reinstall_machine
    actual = machine["disk"].with_name("actual.qcow2")
    machine["disk"].rename(actual)
    machine.update(active_disk=actual, failure=None, install_succeeds=True)
    (kvm_vm.STATE_DIR / "test01.json").unlink()
    assert kvm_vm.cmd_reinstall(machine["args"]) == 0
    assert actual.read_bytes() == b"replacement guest data"
    restored = json.loads((kvm_vm.STATE_DIR / "test01.json").read_text())
    assert Path(restored["disk"]) == actual.resolve()
    assert not machine["disk"].exists()


def test_delete_checks_references_to_manifest_cloud_directory(reinstall_machine):
    machine = reinstall_machine
    cloud = kvm_vm.CLOUD_DIR / "relocated"
    cloud.mkdir()
    shared = cloud / "shared.qcow2"
    shared.write_bytes(b"other guest data")
    manifest = kvm_vm.STATE_DIR / "test01.json"
    state = json.loads(manifest.read_text())
    state["cloud_dir"] = str(cloud)
    manifest.write_text(json.dumps(state))
    machine["other_domain_disk"] = shared
    machine["args"].keep_storage = False
    with pytest.raises(kvm_vm.KVMError, match="referenced by domain"):
        kvm_vm.cmd_delete(machine["args"])
    assert shared.read_bytes() == b"other guest data"
    assert machine["defined"]


class TestCmdReinstall:
    def test_domain_not_found_raises(self):
        args = MagicMock()
        args.name = "missing01"
        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=False):
            with pytest.raises(kvm_vm.KVMError, match="Domain does not exist: missing01"):
                kvm_vm.cmd_reinstall(args)

    def test_domain_not_managed_raises(self):
        args = MagicMock()
        args.name = "unmanaged01"
        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value=None), \
             patch("pathlib.Path.exists", return_value=False):
            with pytest.raises(kvm_vm.KVMError, match="is not managed by kvm-vm"):
                kvm_vm.cmd_reinstall(args)

    def test_running_without_force_raises(self, tmp_path):
        def_file = tmp_path / "web01.yaml"
        def_file.write_text("dummy", encoding="utf-8")
        args = MagicMock()
        args.name = "web01"
        args.force = False
        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value={"definition": str(def_file)}), \
             patch("kvm_vm.domain_state", return_value="running"):
            with pytest.raises(kvm_vm.KVMError, match="VM is running .* Shut it down first, or use --force"):
                kvm_vm.cmd_reinstall(args)

    def test_no_base_image_raises(self, tmp_path):
        raw = {
            "version": 1,
            "vm": {"name": "cloned01"},
            "storage": {"disk_gib": 20},
            "network": {"mac": "52:54:00:11:22:33", "ipv4": {"method": "dhcp"}},
            "cloud_init": {"ssh_authorized_keys": ["ssh-ed25519 AAAA... test"]},
        }
        def_file = tmp_path / "cloned01.yaml"
        kvm_vm.yaml_dump(def_file, raw)

        args = MagicMock()
        args.name = "cloned01"
        args.force = False
        args.os = None
        args.image_url = None
        args.image_path = None
        args.os_variant = None

        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value={"definition": str(def_file)}), \
             patch("kvm_vm.domain_state", return_value="shut off"):
            with pytest.raises(kvm_vm.KVMError, match="has no base image configured. Please specify --os"):
                kvm_vm.cmd_reinstall(args)

    def test_dry_run_updates_os_and_keeps_other_configs(self, tmp_path, capsys):
        raw = {
            "version": 1,
            "vm": {
                "name": "web01",
                "vcpus": 4,
                "memory_mib": 4096,
                "cpu": "host-passthrough",
                "autostart": True,
                "start": True,
                "os_variant": "ubuntu24.04",
            },
            "storage": {
                "disk_gib": 35,
                "bus": "virtio",
                "cache": "none",
                "discard": "unmap",
                "image": {"distro": "ubuntu24.04"},
            },
            "network": {
                "mode": "bridge",
                "bridge": "br0",
                "model": "virtio",
                "mac": "52:54:00:aa:bb:cc",
                "ipv4": {
                    "method": "static",
                    "address": "192.168.1.100/24",
                    "gateway": "192.168.1.1",
                    "dns": ["1.1.1.1"],
                },
                "ipv6": {"method": "disabled"},
            },
            "cloud_init": {
                "user": "sysadmin",
                "ssh_authorized_keys": ["ssh-ed25519 AAAA... sysadmin@kvm"],
                "package_update": True,
                "qemu_guest_agent": True,
                "packages": ["nginx", "curl"],
                "timezone": "Asia/Taipei",
                "runcmd": [["echo", "hello"]],
            },
        }
        def_file = tmp_path / "web01.yaml"
        kvm_vm.yaml_dump(def_file, raw)

        args = MagicMock()
        args.name = "web01"
        args.os = "debian13"
        args.image_url = None
        args.image_path = None
        args.os_variant = None
        args.dry_run = True
        args.force = False

        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value={"definition": str(def_file), "definition_source": str(def_file)}), \
             patch("kvm_vm.domain_state", return_value="shut off"), \
             patch("kvm_vm.validate_network_attachment"):
            ret = kvm_vm.cmd_reinstall(args)
            assert ret == 0

        captured = capsys.readouterr().out
        assert "dry-run: all pre-flight checks passed" in captured
        # Parse the output YAML after the header line
        yaml_text = "\n".join(captured.splitlines()[1:])
        cfg = kvm_vm.yaml.safe_load(yaml_text)

        # OS changed to debian13
        assert cfg["storage"]["image"]["distro"] == "debian13"
        assert cfg["vm"]["os_variant"] == "debian13"

        # All other configs unchanged!
        assert cfg["vm"]["name"] == "web01"
        assert cfg["vm"]["vcpus"] == 4
        assert cfg["vm"]["memory_mib"] == 4096
        assert cfg["storage"]["disk_gib"] == 35
        assert cfg["network"]["mac"] == "52:54:00:aa:bb:cc"
        assert cfg["network"]["mode"] == "bridge"
        assert cfg["network"]["bridge"] == "br0"
        assert cfg["network"]["ipv4"]["method"] == "static"
        assert cfg["network"]["ipv4"]["address"] == "192.168.1.100/24"
        assert cfg["cloud_init"]["user"] == "sysadmin"
        assert cfg["cloud_init"]["packages"] == ["nginx", "curl"]
        assert cfg["cloud_init"]["timezone"] == "Asia/Taipei"

    def test_reinstall_execution_flow(self, tmp_path):
        raw = _minimal_raw(network={"mac": "52:54:00:99:88:77", "ipv4": {"method": "dhcp"}})
        def_file = tmp_path / "test01.yaml"
        kvm_vm.yaml_dump(def_file, raw)

        disk = tmp_path / "test01.qcow2"
        disk.touch()

        args = MagicMock()
        args.name = "test01"
        args.os = "rocky9"
        args.image_url = None
        args.image_path = None
        args.os_variant = None
        args.dry_run = False
        args.yes = True
        args.force = True
        args.refresh_image = False
        args.no_start = False

        mock_base = tmp_path / "base.qcow2"
        mock_base.touch()

        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value={"definition": str(def_file), "disk": str(disk)}), \
             patch("kvm_vm.domain_state", return_value="running"), \
             patch("kvm_vm.validate_network_attachment"), \
             patch("kvm_vm.acquire_base_image", return_value=(mock_base, "rocky9")), \
             patch("kvm_vm.make_disk_from_base") as mock_make_disk, \
             patch("kvm_vm.restore_selinux"), \
             patch("kvm_vm.run", side_effect=lambda cmd, **kw: lifecycle_probe(cmd, disk, **kw)) as mock_run, \
             patch("kvm_vm.undefine_domain") as mock_undefine, \
             patch("kvm_vm.make_cloud_files", return_value=(None, None, None, "iid-123")), \
             patch("kvm_vm.define_vm") as mock_define, \
             patch("kvm_vm.save_state") as mock_save:

            # Make make_disk_from_base create the tmp_disk so tmp_disk.replace succeeds
            def fake_make_disk(base, target, gib):
                target.touch()
            mock_make_disk.side_effect = fake_make_disk

            ret = kvm_vm.cmd_reinstall(args)
            assert ret == 0

            # Verifications
            mock_make_disk.assert_called_once()
            called_tmp_disk = mock_make_disk.call_args[0][1]
            assert str(called_tmp_disk).endswith(".reinstall.tmp")

            mock_run.assert_any_call(["virsh", "-c", kvm_vm.LIBVIRT_URI, "destroy", "test01"])
            mock_undefine.assert_called_once_with("test01")
            mock_define.assert_called_once()
            mock_save.assert_called_once()
            saved_cfg = mock_save.call_args[0][0]
            assert saved_cfg["storage"]["image"]["distro"] == "rocky9"
            assert saved_cfg["network"]["mac"] == "52:54:00:99:88:77"

    def test_make_disk_failure_does_not_destroy_or_undefine_vm(self, tmp_path):
        raw = _minimal_raw()
        def_file = tmp_path / "test01.yaml"
        kvm_vm.yaml_dump(def_file, raw)

        disk = tmp_path / "test01.qcow2"
        disk.touch()

        args = MagicMock()
        args.name = "test01"
        args.os = "debian13"
        args.image_url = None
        args.image_path = None
        args.os_variant = None
        args.dry_run = False
        args.yes = True
        args.force = True
        args.refresh_image = False
        args.no_start = False

        mock_base = tmp_path / "base.qcow2"
        mock_base.touch()

        with patch("kvm_vm.require_commands"), \
             patch("kvm_vm.require_cloud_seed_generator"), \
             patch("kvm_vm.ensure_dirs"), \
             patch("kvm_vm.domain_exists", return_value=True), \
             patch("kvm_vm.load_state", return_value={"definition": str(def_file), "disk": str(disk)}), \
             patch("kvm_vm.domain_state", return_value="running"), \
             patch("kvm_vm.validate_network_attachment"), \
             patch("kvm_vm.acquire_base_image", return_value=(mock_base, "debian13")), \
             patch("kvm_vm.make_disk_from_base", side_effect=kvm_vm.KVMError("Out of disk space")), \
             patch("kvm_vm.run", side_effect=lambda cmd, **kw: lifecycle_probe(cmd, disk, **kw)) as mock_run, \
             patch("kvm_vm.undefine_domain") as mock_undefine:

            with pytest.raises(kvm_vm.KVMError, match="Out of disk space"):
                kvm_vm.cmd_reinstall(args)

            # Crucial assertion: VM was NOT destroyed and NOT undefined!
            assert all(call.args[0][3] not in ("destroy", "undefine")
                       for call in mock_run.call_args_list if call.args[0][0] == "virsh")
            mock_undefine.assert_not_called()
            # Original disk still exists
            assert disk.exists()


# --- distro support tests ---

class TestSupportedDistros:
    @pytest.mark.parametrize("distro", [
        "debian12", "debian13",
        "ubuntu24.04", "ubuntu26.04",
        "rocky8", "rocky9", "rocky10",
        "almalinux8", "almalinux9", "almalinux10",
    ])
    def test_distro_metadata_complete(self, distro):
        assert distro in kvm_vm.DISTROS
        meta = kvm_vm.DISTROS[distro]
        assert meta["url"].startswith("https://")
        assert meta["cache"].endswith((".img", ".qcow2"))
        assert isinstance(meta["os_variant"], str) and len(meta["os_variant"]) > 0

    @pytest.mark.parametrize("distro", [
        "debian12", "ubuntu26.04", "rocky8", "rocky10", "almalinux8", "almalinux10",
    ])
    def test_new_distros_normalize_definition(self, distro):
        raw = _minimal_raw(storage={"image": {"distro": distro}})
        cfg = kvm_vm.normalize_definition(raw)
        assert cfg["storage"]["image"]["distro"] == distro

    @pytest.mark.parametrize("distro", [
        "debian12", "ubuntu26.04", "rocky8", "rocky10", "almalinux8", "almalinux10",
    ])
    def test_new_distros_apply_os_override(self, distro):
        raw = _minimal_raw()
        kvm_vm.apply_os_override(raw, os_arg=distro)
        assert raw["storage"]["image"]["distro"] == distro
        assert raw["vm"]["os_variant"] == kvm_vm.DISTROS[distro]["os_variant"]
