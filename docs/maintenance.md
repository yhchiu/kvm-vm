# Guest maintenance scheduling

[繁體中文](maintenance.zh-TW.md)

Multiple guests sharing a storage device can generate an I/O spike when their
periodic maintenance jobs run together. The optional maintenance policy spreads
selected systemd timers over the week or over a daily maintenance window. No
host scheduler, guest daemon, or additional guest package is required.

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
Existing guests are not modified by editing the saved YAML alone. A future
maintenance management command may support applying policies to running guests;
for now administrators can manage the named drop-ins manually. When removing a
managed drop-in, reload systemd and try-restart the affected timer. Do not
remove other administrator drop-ins or disable maintenance services.
