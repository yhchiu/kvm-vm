# Ticket 06 validation

Fixed review point: `49e88c5`. CPU-compatible acceptance was refreshed on
October 22, 2026 after review identified insufficient SSH rejection evidence.
The matrices below now link to runs using explicit server method-list rejection.
This ticket remains open for four hardware-limited images.
Product compatibility fix: `342ec7e`; authentication probe fix: `4610bc8`.
The operator confirmed that no newer KVM host is available. Rocky Linux 9/10
and AlmaLinux 9/10 remain pending compatible hardware, as documented in
[the earlier image report](../../../tests/REAL_MACHINE_RESULTS.md).

## Environment and scope

Use the same authorized Debian 13 / Xeon L5420 host as ticket 05, with the
default libvirt NAT, existing `br-vhost`, and standalone wired parent `enp4s0`.
The Windows controller uses an existing SSH key and verified host key. NAT SSH
is forwarded through the host; bridge/Macvtap SSH must originate directly from
the independent controller. The private credential directories are root-owned
mode 0700. Credentials are never included in this report.

The host has Python 3.13, Paramiko 3.5.1, and pexpect 4.9. The Windows controller
uses Python 3.11.9 with isolated Paramiko 5.1.0 dependencies. The serial helper
runs on Linux. The workflow and prerequisites are documented in
[CONSOLE_ACCEPTANCE.md](../../../tests/CONSOLE_ACCEPTANCE.md).

## Findings

- The initial serial probe logged in successfully but did not recognize sudo's
  output. Explicit carriage returns and a marker constructed inside the guest
  avoid reliance on terminal line discipline or command echo. Ubuntu 26.04's
  OSC terminal metadata required accepting a marker after terminal controls.
  Regression cases cover ordinary, bracketed-paste, and OSC output, and verify
  that echoed commands cannot satisfy the success pattern. Exceptions and
  cleanup errors remain redacted.
- Ubuntu 26.04 acquired two DHCP leases for the same MAC during boot. Choosing
  the first lease caused a readiness timeout against the stale address while
  the guest itself was healthy. The controller now prefers live agent addresses,
  excludes loopback, and attempts all candidate leases. Regression cases cover
  both agent availability and fallback. The retained Ubuntu guest subsequently
  passed authentication, policy, provisioning-log scans, and a changed-boot-ID
  reboot check, then was deleted while retaining its credential file.
- Fresh Rocky 8 provisioning exposed a product compatibility bug: the image
  provides `/usr/libexec/platform-python` 3.6.8 without a `python3` command.
  The account remained locked. Commit `342ec7e` chooses the available interpreter
  and removes Python 3.7-only subprocess arguments from the guest script.
  The new interpreter-selection and Python 3.6 tests failed before the fix;
  the Linux suite passed 291 tests afterward. Fresh Rocky 8 and AlmaLinux 8 both
  passed Console/sudo/SSH checks before and after reboot with the fix. Both use
  platform Python 3.6.8. Their test guests were deleted and credential files
  retained. AlmaLinux's first cleanup attempt failed while a concurrent clone
  was active; cleanup was retried successfully afterward. The raw failed report
  and the completed report are retained separately.
- An enabled Debian 13 clone accepted its independent credential, rejected the
  source credential, and passed the same checks after reboot. The first
  lifecycle controller stopped before creating the disabled clone during a
  concurrent image operation. Work resumed from the verified source and clone,
  retaining the original partial report, and all remaining lifecycle cases
  passed. The controller now retries only the
  CLI's explicit pre-mutation lock rejection, with a bounded wait, and never
  removes a lock or retries a provisioning failure.

## Image and password-stack matrix

Refreshed passing rows include real serial login and sudo, reachable key SSH,
explicit password and keyboard-interactive rejection, account/expiry/getty/root checks,
private credential permissions, plaintext scans, and a changed-boot-ID reboot
with repeated current-credential and SSH-policy checks. Exact distro package
versions are in each JSON report; the table abbreviates PAM/shadow to upstream
versions. All passwords use SHA-512 crypt. Each SSH negative check records the
server's allowed methods in `ssh_rejection_evidence`.

| Image | cloud-init | Python | PAM / shadow | Result and evidence |
| --- | --- | --- | --- | --- |
| Debian 12 | 22.4.2 | 3.11.2 | 1.5.2 / 4.13 | [Pass, NAT](console-results/console-refresh-debian12.json) |
| Debian 13 | 25.1.4 | 3.13.5 | 1.7.0 / 4.17.4 | [Pass, NAT lifecycle](console-results/console-refresh-lifecycle.json) |
| Ubuntu 24.04 | 26.1-0ubuntu1~24.04.1 | 3.12.3 | 1.5.3 / 4.13 | [Pass, bridge and existing user](console-results/console-refresh-bridge-preexisting.json) |
| Ubuntu 26.04 | 26.1-0ubuntu3~26.04.1 | 3.14.4 | 1.7.0 / 4.17.4 | [Pass, Macvtap](console-results/console-refresh-macvtap-ubuntu26.json) |
| Rocky Linux 8 | 23.4-7.el8_10.0.1 | 3.6.8 | 1.3.1 / 4.6 | [Pass, NAT](console-results/console-refresh-rhel8.json) |
| AlmaLinux 8 | 23.4-7.el8_10.11.alma.2 | 3.6.8 | 1.3.1 / 4.6 | [Pass, NAT](console-results/console-refresh-rhel8.json) |
| Rocky Linux 9 | Not exercised | Not exercised | Not exercised | Pending x86-64-v2 hardware |
| Rocky Linux 10 | Not exercised | Not exercised | Not exercised | Pending x86-64-v3 hardware |
| AlmaLinux 9 | Not exercised | Not exercised | Not exercised | Pending x86-64-v2 hardware |
| AlmaLinux 10 | Not exercised | Not exercised | Not exercised | Pending compatible hardware; this host also lacks v2 |

The original Ubuntu 24.04 cache does not contain an `ubuntu` account. Its
existing-account case used an isolated image copy with a locked `ubuntu` user
and login shell created before CLI provisioning. The report records the
original/fixture hashes and confirms fixture deletion. The upstream cache was
not modified. Existing `admin` accounts were also exercised by the clone cases.

## Mode and lifecycle matrix

| Mode | Explicit console opt-in | Policy omitted: locked default |
| --- | --- | --- |
| NAT | Debian, Ubuntu and RHEL-family cases above | [Pass](console-results/console-refresh-nat-locked.json) |
| Bridge | Ubuntu 24.04 existing-user case above | [Pass after test-IP neighbor refresh](console-results/console-refresh-bridge-locked.json) |
| Macvtap | Ubuntu 26.04 case above | [Pass on retained-VM continuation](console-results/console-refresh-macvtap-locked-resumed.json) |

Both policies were checked again after reboot. Disabled cases reached a real
login prompt and explicitly rejected the test password while key SSH succeeded.
The independent Windows client reached bridge/Macvtap guests directly at the
reserved `.246`; bridge gateway was `.242`, Macvtap gateway `.241`. NAT used the
host forwarding channel. Network failures during boot were readiness retries,
never successful negative-authentication evidence.

The [refreshed lifecycle report](console-results/console-refresh-lifecycle.json)
records these passing cases:

- Enabled source to enabled clone: distinct target credential accepted, source
  credential rejected, and target current-credential/SSH checks repeated after
  reboot.
- Enabled source to disabled clone: inherited password rejected before and after
  reboot; account locked and key SSH retained.
- Source restarted and still accepted its original credential after cloning.
- Disabled source to enabled clone: new credential accepted, ancestor credential
  rejected, and current-credential/SSH checks repeated after reboot.
- Enabled reinstall: fresh credential accepted and old credential rejected;
  current-credential/SSH checks repeated after reboot.
- Disabled reinstall: policy stayed locked; reference password rejected and key
  SSH retained, including after reboot.
- All owned lifecycle guests deleted; all delivered files, including rotated
  credentials, remained available afterward.

The original lifecycle run continued from its retained, verified checkpoint; it was not
silently relabeled as an uninterrupted pass. Its
[first partial report](console-results/console-lifecycle-01.json), the
[initial image run](console-results/console-matrix-02.json), the
[Rocky compatibility failure](console-results/console-rhel8-01.json), and the
[Alma cleanup retry precursor](console-results/console-rhel8-02.json) remain
available alongside the completed evidence. The refreshed lifecycle run passed
uninterrupted with the corrected SSH probe.

The refreshed Macvtap default-lock run stopped on an SSH readiness timeout;
its [failed report](console-results/console-refresh-macvtap-locked.json) is
preserved. Inspection later found direct key SSH, networking, cloud-init and
the locked account healthy. The retained VM passed all checks and reboot in a
separate continuation report. The initial timeout's cause was not established.

When the same reserved `.246` moved back to routed bridge, Windows SSH timed
out while host-forwarded SSH worked. Packet headers on `enp4s0` showed return
traffic still addressed to the deleted Macvtap MAC `52:54:00:6b:e7:59`, rather
than host MAC `00:30:48:d5:41:72`. Three unsolicited ARP replies for that test
IP restored direct SSH and allowed cloud-init to complete. The bridge default
case then passed, including reboot. This was an operator test-environment
repair, not product behavior: no host IP, route, bridge or firewall settings
changed. Diagnostic packages `tcpdump` and `iputils-arping` were installed.
The standard arping command refused the nonlocal source IP, so a one-off raw
Ethernet helper sent the replies without assigning the guest IP to the host.

Final Linux `bash tests/smoke.sh`: **307 passed**, smoke OK. This includes the
real POSIX delivery/ownership, existing/symlink destination, partial provisioning,
retry, and credential/VM preservation tests from tickets 02-04. The standalone
acceptance helpers compile. Tests do not claim rollback after destructive disk
replacement. Provisioning-log scans cover cloud-init's two logs, not every
system log.

## Resource handling

Only generated `e2e-console-*` resources are eligible for harness deletion.
Failed guests and private credentials are retained for diagnosis. The diagnosed
failed Rocky 8 guest was deleted before a fresh run with the compatibility fix;
its credential was retained. `vh-mon` was gracefully stopped to reserve `.246`
and capacity for network-mode tests, then restored after the address was released.
Its disk and definition were preserved.

The [refreshed cleanup audit](console-results/console-refresh-cleanup.json) shows no owned domains,
managed artifacts, fixture disks, or lifecycle lock. Original `vh-control`,
`vh-processing`, `vh-pb`, and `vh-mon` are running; `acsvm-vm-910001` remains shut
off. Twenty-nine credential/reference files remain root-owned mode 0600 under
root-owned mode-0700 private directories. No password or hash is published.

## Review gate

Initial Standards review found no blocking issues. Spec review found that the
SSH negative-authentication probe accepted generic Paramiko authentication
exceptions, including timeout and connection loss. Historical reports above
therefore do not establish explicit SSH method rejection; their console and
other independent checks remain recorded. The refreshed matrix supersedes
their SSH evidence with 23 recorded check pairs (46 explicit method rejections).

The corrected probe accepts only a server method-list rejection that excludes
the attempted method and records that list in each result. Regression tests
cover both methods with explicit rejection, successful authentication, timeout,
connection loss, generic failure, and a still-advertised method. Focused review
of the fix and refreshed evidence is pending. Hardware-limited acceptance
remains explicitly incomplete; ticket 06 is not closed.
