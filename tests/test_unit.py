"""Unit tests for kvm-vm pure-Python functions.

Run with: python3 -m pytest tests/test_unit.py -v
"""

from __future__ import annotations

import importlib.util
import io
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


# --- resolve_ssh_keys() ---

class TestResolveSshKeys:
    def test_literal_key(self, tmp_path):
        keys = kvm_vm.resolve_ssh_keys(["ssh-ed25519 AAAA... test"], tmp_path)
        assert keys == ["ssh-ed25519 AAAA... test"]

    def test_file_key(self, tmp_path):
        pub = tmp_path / "id.pub"
        pub.write_text("ssh-ed25519 AAAA... fromfile\n# comment\n\n", encoding="utf-8")
        keys = kvm_vm.resolve_ssh_keys([f"file:{pub}"], tmp_path)
        assert keys == ["ssh-ed25519 AAAA... fromfile"]

    def test_missing_file(self, tmp_path):
        with pytest.raises(kvm_vm.KVMError, match="not found"):
            kvm_vm.resolve_ssh_keys(["file:/nonexistent/key.pub"], tmp_path)

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

