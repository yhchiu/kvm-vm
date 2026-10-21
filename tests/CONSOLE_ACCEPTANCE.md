# Console authentication acceptance

This workflow exercises the real CLI, `virsh console`, guest account policy,
and reachable SSH services. It is opt-in and creates unique `e2e-console-*`
guests. Run it only on an authorized KVM host with capacity for four additional
2 GiB guests during lifecycle tests. It does not alter host network configuration.
The harness waits up to `--timeout` for the CLI's pre-mutation lifecycle-lock
rejection. It never removes a lock or retries a failed provisioning operation.

The controller needs Python 3.9+, PyYAML, Paramiko, OpenSSH `ssh-keygen`, an SSH
key accepted by the host, and a verified host key in the controller's known_hosts.
The Linux host needs the normal project dependencies and Python pexpect. Copy
`serial_auth_probe.py` to the host and use the CLI revision under test.
Optional controller dependencies need not be installed in the product environment.
Requested distro base images must already be present in the CLI's default cache;
the controller records their SHA-256 before provisioning. This workflow assumes
the default CLI state, definition, and image directories.

```sh
python tests/console_acceptance.py \
  --host root@KVM_HOST --host-port 22 --key /private/test-key \
  --remote-cli /root/kvm-vm/kvm-vm \
  --remote-probe /root/kvm-vm/tests/serial_auth_probe.py \
  --distros debian13 --lifecycle \
  --report console-lifecycle.json --execute
```

Use `--distros` with comma-separated supported distro names for the image
matrix. Every basic case checks login again after a graceful stop/start and
requires a changed boot ID. `--lifecycle` additionally checks enabled-source
clones with both target policies, an enabled clone of a disabled source,
source-password preservation, and enabled/disabled reinstall. It requires NAT
so concurrent guests receive separate addresses without consuming reserved LAN
addresses. A failure stops the run and preserves its guests and credentials.

Use `--locked-default` separately to exercise the default locked account. The
harness creates a synthetic private test credential and requires an explicit
serial authentication rejection; an unavailable console cannot pass. Use
`--user ubuntu --preexisting-user --distros ubuntu24.04` to exercise an account
already present before provisioning. This checks the base `/etc/passwd`. If the
account is absent, it creates a private image copy and uses `virt-customize` to
add a locked account with a login shell before calling the real CLI. The report
records the original and derived image hashes and fixture path. The upstream
cache is unchanged; the fixture is deleted after success and retained on failure.
This is an explicit test fixture, not evidence that upstream images precreate
their usual cloud account.

For bridge or Macvtap, add these arguments and omit `--lifecycle`:

```sh
--mode macvtap --attachment eno1 \
--address RESERVED_IP/PREFIX --gateway GATEWAY
```

For bridge use `--mode bridge --attachment EXISTING_BRIDGE`. Reserve the address
and ensure no other VM uses it. The controller must reach that guest directly;
Macvtap authentication probes do not originate from the KVM parent interface.
NAT uses a host SSH TCP forwarding channel. Each negative SSH authentication
attempt verifies the server key observed by successful key login. Connection
failures, timeouts, or mismatched keys fail the test.

The serial probe accepts only test-owned consoles. It logs out a shell left by
an interrupted prior probe before submitting the credential. Terminal buffers
and exceptions are never published. A dynamically constructed sudo marker
prevents terminal command echo from being mistaken for successful execution.

Credentials remain in memory and in newly created files under a private host
directory. Reports contain paths and outcomes, never passwords or password
hashes. The scanner compares the actual test password against captured CLI
output, saved state/definitions, provisioning artifacts, and guest cloud-init
logs without printing a
match. Do not enable Paramiko/pexpect payload logging or dump credential files.
This scanner does not claim to inspect every host or guest system log.

Successful cases delete their owned VMs and verify credential files remain.
Credentials require explicit operator cleanup, including after VM deletion.
If a guest password is subsequently changed, its initial credential file is
stale. Missing credentials or a guest unable to reach a login prompt require
another authorized recovery path. Failed cases retain resources for diagnosis;
review the report's `retained_guests` and private directory before cleanup.

Run `bash tests/smoke.sh` on Linux as well. These tests cover protected delivery
and lifecycle failure boundaries that the successful real-machine cases do not
simulate. A passing subset cannot satisfy the full image/mode/lifecycle matrix;
keep missing hardware and incomplete cases explicitly pending.
