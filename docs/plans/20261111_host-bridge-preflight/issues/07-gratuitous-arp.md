# 07: Announce the host addresses after a move

**Status:** complete

**What happened:** On Host A, auto-rollback put `216.244.76.242/29` and MAC
`00:30:48:d5:41:72` back on `enp4s0` at 21:15:11. The host itself was
reachable locally, but ICMP and TCP/22222 from outside timed out until
21:21:53 (about 6 minutes 42 seconds). A short create-then-immediate
rollback did not drop the existing SSH session. The likely cause is the
upstream switch or gateway holding the previous MAC location.

**Decision:** After `create` moves addresses onto the bridge, and after
`rollback.sh` puts them back on the uplink, send a gratuitous ARP for each
global IPv4 and an unsolicited Neighbor Advertisement for each global IPv6.
Prefer `arping -c 3 -U` and `ndptool -t na -U`. If those are missing, send
the frames from Python. If that fails, try `ndisc6` for IPv6 only and say
in the log that it is a solicitation. Never fail the restore because an
announcement could not be sent. `rollback.sh` inlines the fallback so the
watchdog does not need to import the tool.

**Tests:** Frame layout, command selection, the generated rollback script,
and `apply_runtime` calling the announcer. No live uplink.

- [x] Announce on the bridge after create
- [x] Announce on the uplink from rollback.sh (auto and explicit)
- [x] Degrade when arping, ndptool and the packet send are unavailable
- [x] Document the behavior in both languages
