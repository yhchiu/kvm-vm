import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "registrations", Path(__file__).resolve().parents[1] / "tools/monitoring/registrations.py"
)
reg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reg)
HOST = "2f3c4e13-46e4-44ce-ae61-2886cb3ba8cc"


def test_handoff_cannot_reassign_an_existing_credential(tmp_path):
    path = tmp_path / "registry"
    path.touch(mode=0o640)
    records, hashes = reg.read(path)
    reg.stage(records, "first", HOST, "customer-a", 18429, 18428)
    reg.stage(records, "second", HOST, "customer-b", 28429, 28428)
    reg.change(records, hashes, "activate", "first", lambda _: "old-verifier")
    with pytest.raises(ValueError):
        reg.stage(records, "first", HOST, "customer-b", 38429, 28428)
    with pytest.raises(ValueError):
        reg.change(records, hashes, "activate", "second", lambda _: "new")

    def unavailable(_):
        raise OSError("hashing failed")

    with pytest.raises(OSError):
        reg.change(records, hashes, "rotate", "first", unavailable)
    assert hashes == {"first": "old-verifier"}
    reg.change(records, hashes, "rotate", "first", lambda _: "rotated")
    reg.write(path, records, hashes)
    assert path.stat().st_mode & 0o777 == 0o640
    records, hashes = reg.read(path)
    assert hashes == {"first": "rotated"}
    reg.change(records, hashes, "revoke", "first", unavailable)
    reg.change(records, hashes, "activate", "second", lambda _: "new")
    reg.write(path, records, hashes)
    records, hashes = reg.read(path)
    assert records["first"]["state"] == "revoked"
    assert records["first"]["tenant"] == "customer-a"
    assert hashes == {"second": "new"}
    with pytest.raises(ValueError):
        reg.change(records, hashes, "activate", "first", unavailable)
    # Multiple registrations may use their own tenant's store, with distinct relays.
    reg.stage(records, "third", HOST, "customer-a", 18430, 18428)
    with pytest.raises(ValueError):
        reg.stage(records, "fourth", HOST, "customer-b", 18431, 18428)
