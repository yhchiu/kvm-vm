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
             patch("kvm_vm.run") as mock_run, \
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
             patch("kvm_vm.run") as mock_run, \
             patch("kvm_vm.undefine_domain") as mock_undefine:

            with pytest.raises(kvm_vm.KVMError, match="Out of disk space"):
                kvm_vm.cmd_reinstall(args)

            # Crucial assertion: VM was NOT destroyed and NOT undefined!
            mock_run.assert_not_called()
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


