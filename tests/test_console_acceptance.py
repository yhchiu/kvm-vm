"""Regression checks for real-machine acceptance evidence."""
from types import SimpleNamespace

import pytest

pytest.importorskip("paramiko")
import console_acceptance


@pytest.mark.parametrize("agent_available", [True, False])
def test_nat_readiness_considers_multiple_leases_and_prefers_live_agent(agent_available):
    session = object.__new__(console_acceptance.Acceptance)
    session.args = SimpleNamespace(mode="nat")

    def command(*args, **kwargs):
        if args[-1] == "agent":
            return (0, "lo ipv4 127.0.0.1/8\neth0 ipv4 192.0.2.96/24", "") if agent_available else (1, "", "unavailable")
        return 0, "vnet1 ipv4 192.0.2.97/24\nvnet1 ipv4 192.0.2.96/24", ""

    session.command = command
    expected = ["192.0.2.96", "192.0.2.97"] if agent_available else ["192.0.2.97", "192.0.2.96"]
    assert session.addresses("test-owned-vm") == expected


@pytest.mark.parametrize("failure,timeout,retries", [
    ("Another lifecycle operation or interrupted attempt owns /state/.lifecycle-lock", 30, True),
    ("Provisioning failed after credential delivery", 30, False),
    ("Another lifecycle operation or interrupted attempt owns /state/.lifecycle-lock", 0, False),
])
def test_cli_retries_only_pre_mutation_lock_rejections(monkeypatch, failure, timeout, retries):
    session = object.__new__(console_acceptance.Acceptance)
    session.args = SimpleNamespace(remote_cli="/test/kvm-vm", timeout=timeout)
    session.outputs = []
    calls = []

    def command(*args, **kwargs):
        calls.append(args)
        return (1, "", failure) if len(calls) == 1 else (0, "created", "")

    session.command = command
    monkeypatch.setattr(console_acceptance.time, "sleep", lambda delay: None)
    if retries:
        assert session.cli("create", "test.yaml")[0] == 0
        assert len(calls) == 2
    else:
        with pytest.raises(RuntimeError):
            session.cli("create", "test.yaml")
        assert len(calls) == 1
