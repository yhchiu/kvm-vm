English | [繁體中文](bridge-host.zh-TW.md)

# Host bridge setup

`kvm-vm` attaches a VM to a Linux bridge that already exists. It does not
create that bridge, and it does not move the host address. `kvm-vm-bridge` is
an optional tool that does that one job: it creates a bridge on an uplink,
moves the host address and default route onto the bridge, and rolls the
change back if you do not confirm it.

Use it only when you have a way back: the timer below, an out-of-band console,
or a host you can reinstall. A mistake on the only management interface drops
SSH until the timer fires.

## Requirements

- Linux, run as root
- `ip` (iproute2)
- `systemd-run` for the rollback timer. Without it, the tool falls back to a
  detached `nohup` process. That fallback does not survive a parent systemd
  unit that kills its whole control group, so prefer `systemd-run`.
- Optional: `ping` (checks the previous gateway after the move)
- Optional: `nmcli` when NetworkManager should persist the bridge,
  `systemctl` to detect the network service

| Backend | Typical hosts | How it is detected |
|---|---|---|
| ifupdown | Debian, Ubuntu (`networking.service`) | `/etc/network/interfaces` mentions the uplink |
| NetworkManager | AlmaLinux, RHEL, Fedora | `nmcli` and a NetworkManager-managed uplink |
| systemd-networkd | some Debian and Ubuntu servers | `systemd-networkd` plus a matching `.network` file |
| runtime only | anything else | The runtime change still works. `confirm` skips persist. |

## Install

`sudo ./install.sh` installs `/usr/local/sbin/kvm-vm` and
`/usr/local/sbin/kvm-vm-bridge`. To install the tool on its own:

```bash
sudo install -Dm0755 tools/kvm-vm-bridge /usr/local/sbin/kvm-vm-bridge
```

## Usage

```text
kvm-vm-bridge detect --uplink enp4s0
kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 120 --dry-run
kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 120
kvm-vm-bridge confirm --bridge br0
kvm-vm-bridge status [--bridge br0]
kvm-vm-bridge rollback --bridge br0 --yes
```

| Option | Meaning |
|---|---|
| `--uplink` | Interface that currently holds the host address |
| `--bridge` | Bridge to create (default `br0`) |
| `--timeout` | Seconds to wait for `confirm` before rolling back (default 120) |
| `--dry-run` | Print the plan and the detected backend. Change nothing. |
| `--no-persist` | On confirm, cancel the timer and do not write persistent config |
| `--yes` | Required for `rollback` |

On a Debian ifupdown host whose only uplink is `enp4s0`:

```bash
sudo kvm-vm-bridge detect --uplink enp4s0
sudo kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 180 --dry-run
sudo kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 180
# If this SSH session still works:
sudo kvm-vm-bridge confirm --bridge br0
```

If SSH drops, wait for the timeout. The host should put the address back on
`enp4s0` by itself. Reconnect and read `/var/log/kvm-vm-bridge.log`.

To undo a confirmed bridge later:

```bash
sudo kvm-vm-bridge rollback --bridge br0 --yes
```

That rollback has no second timer. Prefer a console when the host is remote.

## What create does

1. Snapshot addresses, routes and network config under
   `/var/lib/kvm-vm-bridge/sessions/<bridge>/`.
2. Arm the rollback timer **before** any network change. The timer is a
   `systemd-run` oneshot started with `--no-block`, so `create` returns
   without waiting out the timeout. If you confirm, a `CONFIRMED` stamp makes
   the timer exit without rolling back.
3. Create the bridge with **STP off** and **forward_delay 0**, so it does not
   sit in a listening state for about 30 seconds.
4. Copy the uplink MAC onto the bridge.
5. Add the host addresses on the bridge, enslave the uplink, remove those
   addresses from the uplink, and move the default route.
6. Send a gratuitous ARP for each moved IPv4 address, and an unsolicited
   Neighbor Advertisement for each moved IPv6 address, so the switch and
   the gateway learn that the host MAC is now on the bridge.
7. Ping the previous default gateway. Failure rolls the runtime change back
   immediately.

`rollback` does the same announcements after the addresses are back on the
uplink. That covers the auto-rollback timer and an explicit `rollback --yes`.
A missing `arping` or `ndptool` does not fail the restore: the tool sends the
packets itself, and if that also fails it logs the skip and continues.
`ndisc6` is only a last resort for IPv6, and it sends a solicitation rather
than an unsolicited advertisement.

`confirm` cancels the timer and, unless `--no-persist`, writes persistent
configuration. It does not restart NetworkManager, ifupdown or networkd: the
runtime configuration is already in place. A later reboot uses the new files.

Persistent config is not written during the trial window. A reboot before
`confirm` therefore comes back with the previous settings.

`rollback` restores runtime networking from the snapshot, removes the files or
NetworkManager connections this tool wrote, restores the original ifupdown
`interfaces` file when it was modified, and archives the session.

The auto-rollback timer archives the session too. After it fires, `status`
shows no live session and another `create` for the same bridge name is
allowed. A failed `create` that already rolled back is kept under `failed/`
so you can inspect it; it does not block the next `create`.

## Logs and state

| Path | Content |
|---|---|
| `/var/log/kvm-vm-bridge.log` | Timestamped log |
| `/var/lib/kvm-vm-bridge/sessions/<bridge>/` | Live session: `meta.json`, snapshots, `rollback.sh` |
| `/var/lib/kvm-vm-bridge/archived/` | Sessions after a successful rollback |
| `/var/lib/kvm-vm-bridge/failed/` | Sessions after a failed create that was rolled back |

Override the paths with `KVM_VM_BRIDGE_STATE_DIR` and `KVM_VM_BRIDGE_LOG`.

## Firewall warning

`create` (including `--dry-run`) and `confirm` warn when bridged guest IPv4
is likely to be dropped by the host firewall. This is common on Docker hosts:
`br_netfilter` is loaded, `net.bridge.bridge-nf-call-iptables` is `1`, and
the iptables FORWARD policy is DROP, with ACCEPT rules only for Docker and
libvirt bridges.

The warning does not change any rule and does not refuse the command. The
host can still reach a guest on the same bridge, because that traffic does
not use FORWARD. `kvm-vm create`, `clone` and `reinstall` print the same
warning for a bridge that already exists. `kvm-vm validate` does not inspect
the host. A per-bridge `nf_call_iptables` of 0 does not hide the warning:
while the global sysctl is 1, the kernel still filters that bridge. See
[Bridged guests and the host firewall](networking.md#bridged-guests-and-the-host-firewall).

## Safety

- The tool refuses an uplink that is missing, down, already a bridge, or
  already enslaved, and it refuses to reuse an existing bridge name.
- It does not change firewall rules, sysctl or libvirt networks.
- Do not run `create` inside a short-lived systemd unit. When the unit exits
  it can kill a `nohup` fallback timer. The `systemd-run` timer is a separate
  unit and is not affected.

## Tests

`python3 -m pytest tests/test_bridge_tool.py -q` is offline: it does not
create a bridge. A live dry-run is skipped unless `KVM_VM_BRIDGE_LIVE=1` and
`KVM_VM_BRIDGE_LIVE_UPLINK` name a disposable uplink. Do not point that at
the only interface of a production host. The auto-rollback acceptance test
(create, do not confirm, wait, confirm SSH still works) is a manual check on
a disposable host.
