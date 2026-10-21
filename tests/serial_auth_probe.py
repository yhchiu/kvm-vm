"""Run on the KVM host: test a console credential without exposing terminal data."""
import argparse
import json
from pathlib import Path
import sys
import uuid

import pexpect


def sudo_probe(token):
    # Splitting the marker prevents command echo from satisfying the pattern.
    command = "test $(sudo -n id -u) = 0 && printf '%s%s\\n' " + token[:12] + " " + token[12:] + "\r"
    return command, token + r"[\r\n]"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--credential", required=True)
    parser.add_argument("--reject", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    child = None
    phase = "credential"
    try:
        credential = json.loads(Path(args.credential).read_text())
        phase = "connect"
        child = pexpect.spawn("virsh", ["console", args.name, "--force"],
                              encoding="utf-8", timeout=args.timeout, echo=False)
        child.logfile = None
        child.expect("Escape character")
        child.send("\r")
        phase = "login prompt"
        prompt = child.expect([r"login:\s*$", r"[#$] "])
        if prompt == 1:
            # Only test-owned consoles are supported; recover our interrupted shell.
            child.send("exit\r")
            child.expect(r"login:\s*$")
        child.send(credential["user"] + "\r")
        child.expect(r"[Pp]assword:\s*$")
        phase = "authentication"
        child.send(credential["password"] + "\r")
        outcome = child.expect([r"Login incorrect|Authentication failure", r"[#$] "])
        if args.reject:
            if outcome != 0:
                raise RuntimeError("Unexpected authentication success")
            print(json.dumps({"console_rejection": "pass"}))
            return 0
        if outcome != 1:
            raise RuntimeError("Authentication rejected")
        phase = "sudo"
        token = "CONSOLE_OK_" + uuid.uuid4().hex
        # Construct the success marker in the guest so terminal echo cannot pass.
        command, pattern = sudo_probe(token)
        child.send(command)
        child.expect(pattern)
        child.send("exit\r")
        child.expect(r"login:\s*$")
        print(json.dumps({"console_login": "pass", "sudo": "pass", "forced_password_change": False}))
        return 0
    except Exception:
        # pexpect exceptions include terminal buffers, so never print/re-raise them.
        print(json.dumps({"status": "failed", "phase": phase, "terminal_output": "redacted"}))
        return 1
    finally:
        if child is not None:
            try:
                child.sendcontrol("]")
                child.close(force=True)
            except Exception:
                pass  # Terminal buffers must never escape through cleanup errors.


if __name__ == "__main__":
    sys.exit(main())
