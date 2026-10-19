# VM Management

This context covers VM network attachment and distinguishes recovery from an
unwanted change from recovery after loss or corruption of a VM or its storage.

## Language

### Network attachment

**Macvtap attachment**:
A VM network connection directly through a host parent interface to the LAN.
It does not provide a direct management path between the VM and that host
through the parent interface.

**Parent interface**:
The existing host network interface through which a Macvtap attachment reaches
the LAN.

**Macvtap bridge mode**:
The Macvtap forwarding mode that allows guests attached to the same parent in
this mode to communicate locally. It is distinct from a host Linux bridge.
_Avoid_: Linux bridge

### Recovery

**Snapshot**:
A point-in-time recovery point used to roll a VM back after an unwanted change.
A snapshot alone does not establish an independently retained backup.
_Avoid_: Backup

**Backup**:
An independently retained copy of VM data and recovery information intended to
support recovery after loss or corruption of the original VM or its storage.
_Avoid_: Snapshot

**Restore**:
Recovery of a VM from a backup to a usable state after loss or corruption.
_Avoid_: Snapshot rollback

**Backup checkpoint**:
A reference point used to identify disk changes for an incremental backup.
It does not itself contain the historical data needed to recover a VM.
_Avoid_: Snapshot, backup
