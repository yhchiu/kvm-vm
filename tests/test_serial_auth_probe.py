"""Secret-bearing terminal failures must never become test output."""
import json
import re
import sys

import pytest

pexpect = pytest.importorskip("pexpect")
import serial_auth_probe


@pytest.mark.parametrize("prefix", ["\r\n", "\x1b[?2004l\r", "\x1b]3008;type=command\x1b\\"])
def test_sudo_marker_ignores_terminal_controls_but_not_command_echo(prefix):
    token = "CONSOLE_OK_0123456789abcdef"
    command, pattern = serial_auth_probe.sudo_probe(token)
    assert re.search(pattern, command) is None
    assert re.search(pattern, prefix + token + "\r\n") is not None


def test_terminal_exception_and_cleanup_are_redacted(tmp_path, monkeypatch, capsys):
    secret = "synthetic-console-secret-do-not-print"
    credential = tmp_path / "credential.json"
    credential.write_text(json.dumps({"user": "admin", "password": secret}))

    class Terminal:
        logfile = None

        def expect(self, *args):
            raise pexpect.TIMEOUT("terminal buffer: " + secret)

        def sendcontrol(self, *args):
            raise pexpect.EOF("cleanup buffer: " + secret)

    monkeypatch.setattr(pexpect, "spawn", lambda *args, **kwargs: Terminal())
    monkeypatch.setattr(sys, "argv", ["serial_auth_probe", "test-vm", "--credential", str(credential)])
    assert serial_auth_probe.main() == 1
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    assert json.loads(output.out)["terminal_output"] == "redacted"


def test_invalid_credential_is_a_redacted_failure(tmp_path, monkeypatch, capsys):
    credential = tmp_path / "credential.json"
    credential.write_text("synthetic-secret-invalid-json")
    monkeypatch.setattr(sys, "argv", ["serial_auth_probe", "test-vm", "--credential", str(credential)])
    assert serial_auth_probe.main() == 1
    output = capsys.readouterr()
    assert "synthetic-secret" not in output.out + output.err
    assert json.loads(output.out)["phase"] == "credential"
