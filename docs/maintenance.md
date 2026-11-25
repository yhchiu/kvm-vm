# Guest maintenance scheduling

[繁體中文](maintenance.zh-TW.md)

Multiple guests sharing a storage device can generate an I/O spike when their
periodic maintenance jobs run together. The optional maintenance policy spreads
selected systemd timers over the week or over a daily maintenance window. No
host scheduler or new guest daemon is required. Live management uses the existing QEMU Guest Agent.

## Quick start

Add this to a VM definition (the supplied examples opt in):

```yaml
cloud_init:
  maintenance:
    profile: balanced
```

Omitting the policy is equivalent to `profile: none`: creating or reinstalling
an existing definition does not configure any guest timers by default. This
feature does not change a running VM simply by upgrading kvm-vm.

The `balanced` profile configures these timers if present in the guest:

| Timer | Frequency | Local time window |
|---|---|---|
| `fstrim.timer` | weekly | 01:00–06:00 |
| `logrotate.timer` | daily | 00:00–06:00 |
| `man-db.timer` | daily | 01:00–06:00 |
| `plocate-updatedb.timer` | daily | 01:00–06:00 |
| `e2scrub_all.timer` | weekly | 01:00–06:00 |

APT/DNF caches, automatic security updates, and other timers are unchanged
unless explicitly selected. Cron jobs and application-specific schedulers are
outside the scope of this feature.

## Customize

```yaml
cloud_init:
  timezone: Asia/Taipei
  maintenance:
    enabled: true
    profile: balanced
    strategy: staggered
    timers:
      fstrim.timer:
        window: "02:00-05:00"
      man-db.timer:
        enabled: false
      my-backup.timer:
        frequency: weekly
        window: "01:00-04:00"
```

| Field | Meaning |
|---|---|
| `profile` | `none` (default), `balanced`, or `custom` (no preset timers). |
| `enabled` | Defaults to true for `balanced` and `custom`, false for `none`. `false` suppresses the policy. |
| `strategy` | `staggered` (default and only supported strategy). |
| `timers` | Mapping from concrete systemd timer unit names to policies. Overrides merge with the selected profile. |
| `timers.<name>.enabled` | Defaults to true; false excludes that timer from kvm-vm management, not from systemd. |
| `timers.<name>.frequency` | `daily` or `weekly`. Required for new enabled timers. |
| `timers.<name>.window` | `HH:MM-HH:MM`, same day, end exclusive; `24:00` allowed as the end. Required for new enabled timers. |

A custom profile must include at least one enabled timer when enabled. Unknown
fields and invalid timer names or windows fail validation. Use a concrete unit
name, not a glob or uninstantiated template. The policy overrides both calendar
and monotonic triggers; only select jobs that can safely be rescheduled this
way. Other unit settings, including `Persistent`, are not changed.

## How it works

SHA-256 of a versioned namespace, effective VM name, resolved MAC address and
timer name selects a minute in the window (and a weekday for weekly timers).
The effective definition stores the name, MAC, and expanded policy. Reinstall
with the same inputs preserves the selected minute and weekday; clone normally
uses a different identity. Name/MAC collisions or hash slot collisions are
possible; this is not an exclusive scheduling or I/O admission-control system.

A generated drop-in looks like this (example only):

```ini
[Timer]
OnCalendar=
OnCalendar=Wed *-*-* 04:36:00
RandomizedDelaySec=15min
AccuracySec=1s
```

The short jitter (up to 15 minutes, reduced for narrow windows) and one-second
accuracy allowance fit the nominal calendar window. Timers use the guest's
local timezone. Actual activation can still be outside the window after
shutdown, clock/DST changes, service delays, or persistent catch-up; later
administrator or vendor drop-ins can also alter the effective schedule.

Cloud-init appends a first-boot `runcmd` step after the user's commands and
kvm-vm provisioning. It inspects systemd LoadState, skips absent or masked
units, writes only `/etc/systemd/system/<timer>.d/90-kvm-vm-maintenance.conf`,
reloads systemd and uses `try-restart` for changed units. It does not enable
or start inactive timers. User drop-ins are left intact. Identical files do
not trigger a reload. The operation is logged in cloud-init output; unexpected
systemctl or filesystem errors fail the maintenance step.

## Lifecycle

| Operation | Behavior |
|---|---|
| `validate` | Check and expand the policy, offline. |
| `create` | Generate policy in the seed; guest applies at first boot. |
| `clone` | Remove only kvm-vm maintenance drop-ins from the copied disk during offline sysprep, even for profile `none`. Target policy is generated independently. |
| `reinstall` | Generate from the effective definition for the new system disk. |
| Backup restore / snapshot rollback | Preserve the captured guest filesystem and configuration. |
| Reboot / migration | Keep the installed drop-ins. |
| `maintenance apply` / `remove` | Reconcile a running guest, then save the effective policy for future reinstall. |

Cloud-init runs its user commands late in first boot. An original timer may
already have run before the policy takes effect, especially after clone or
when a package is installed. Persistent timestamps are deliberately not
cleared; a missed job may catch up outside its regular window. Staggering does
not limit the duration or concurrency of running jobs, and does not replace
host-side IOPS/bandwidth limits or a coordinated backup schedule.

## Verify in the guest

```sh
cloud-init status --wait
systemctl list-timers --all
systemctl cat fstrim.timer
systemctl show fstrim.timer -p TimersCalendar -p RandomizedDelayUSec -p AccuracyUSec -p Persistent -p NextElapseUSecRealtime
journalctl -u fstrim.service --since '30 days ago'
```

Check `/var/log/cloud-init-output.log` for `kvm-vm maintenance:` messages.
Existing guests are not modified by editing the saved YAML alone.

## Manage existing VMs from the host

```sh
sudo kvm-vm maintenance status web01
sudo kvm-vm maintenance preview web01 --profile balanced
sudo kvm-vm maintenance apply web01 --profile balanced --dry-run
sudo kvm-vm maintenance apply web01 --profile balanced --yes
sudo kvm-vm maintenance remove web01 --yes
```

- `preview` prints the proposed policy and generated drop-ins without a guest
  agent connection. It works for shut-off managed VMs too.
- `status` prints the saved desired policy, all live systemd timers, and the
  actual managed drop-ins and timer properties for desired or previously
  managed units. Compare the desired and effective values; other drop-ins
  may override them. It does not change the guest or saved definition.
- `apply` replaces the managed policy (or reapplies the saved policy if no
  option is given). It removes obsolete kvm-vm drop-ins and configures the
  selected installed, unmasked timers. `remove` reconciles to `profile: none`.
  Neither command disables a timer, enables an inactive timer, or removes
  other administrators' drop-ins. Live reconciliation reloads systemd and
  try-restarts affected active timers, including selected unchanged timers,
  so retries can recover from earlier reload failures.

For custom settings, supply a standalone policy mapping (not a complete VM YAML):

```yaml
# maintenance.yaml
profile: balanced
timers:
  fstrim.timer:
    window: "02:00-05:00"
  man-db.timer:
    enabled: false
```

```sh
sudo kvm-vm maintenance preview web01 --policy maintenance.yaml
sudo kvm-vm maintenance apply web01 --policy maintenance.yaml --yes
```

`--profile` and `--policy` are mutually exclusive. `--dry-run` is a host-side
preview, not a guest connectivity check. Live changes require affirmative
confirmation (`[y/N]`), or `--yes` in automation. Both change commands take
the existing host lifecycle lock. They require a running managed VM, a
matching saved MAC, no active libvirt domain job, a Linux systemd guest with
QEMU Guest Agent `guest-exec` / `guest-exec-status` enabled, GNU `timeout` and
`flock` (for mutations). No SSH access is needed. If cloud-init exists, it
must report `done` or `disabled` with a successful exit code before applying
or removing a policy; resolve first-boot errors before retrying. Guest
commands have a 60-second runtime limit (plus 5 seconds to terminate) and
bounded host RPC/polling deadlines. The guest lock prevents overlapping
live changes.

On success the effective definition in `/etc/kvm-vm/definitions/NAME.yaml`
is atomically replaced, preserving other values and permissions. This does
not rewrite the original input YAML or the existing cloud-init seed; ordinary
reboot retains the new drop-ins, and reinstall generates a new seed from the
updated definition. Do not manually rerun the old cloud-init seed with
`cloud-init clean` without reconciling the policy afterwards. A snapshot
rollback or backup restore returns to the captured state. Coordinate external
backup and migration operations separately.

The guest filesystem and host definition cannot be updated in one atomic
transaction. If the agent disconnects, a systemctl operation fails, or the
host cannot save the definition, the command returns an error and the guest
may be partly changed. Wait for the bounded guest command to finish, inspect
`status`, and retry with the same explicit policy. If an obsolete drop-in was
already removed before a failed timer restart, inspect that timer and use
`systemctl daemon-reload` and `systemctl try-restart NAME.timer` inside the
guest if necessary. Persistent catch-up can still run immediately after a
schedule change. Do not expose these administrator commands as tenant APIs.

### Real-host acceptance checklist

On a disposable running managed VM, wait for cloud-init and verify that the
agent responds. Record `systemctl list-timers --all` and any administrator
`fstrim.timer.d` files. Preview, apply balanced, and compare `status` with
`systemctl cat fstrim.timer` and `systemctl show fstrim.timer`. Reboot and
confirm the drop-in remains. Apply a narrower window, verify the new
schedule, then remove and verify that only the reserved drop-in disappeared
and the timer's original enablement is unchanged. Repeat on at least one
Debian/Ubuntu and one RHEL-family guest. Do not use a production VM as the
first acceptance target.
