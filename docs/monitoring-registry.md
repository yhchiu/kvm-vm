# Central VM and tenant registry

Operator metadata only, **not authorization**. Central remains administrator-only and `customer-readiness` remains BLOCKED. The registry is independent of the TSDB and existing Host Inventory; Host tenant metadata is never inherited by VMs. A shared host can contain VMs owned by multiple tenants.

Register hosts in `inventory` first. Use actual UUIDs from `virsh domuuid VM` or `kvm-vm monitor --json`. Replace VM_UUID and HOST_UUID with canonical lowercase UUIDs.

```sh
sudo ./tools/setup-central-monitoring.sh registry tenant-add cust-a 'Customer A'
sudo ./tools/setup-central-monitoring.sh registry tenant-add cust-b 'Customer B'
sudo ./tools/setup-central-monitoring.sh registry vm-add VM_UUID HOST_UUID web01 --tenant cust-a
sudo ./tools/setup-central-monitoring.sh registry list
sudo ./tools/setup-central-monitoring.sh registry vm-move VM_UUID OTHER_HOST_UUID
sudo ./tools/setup-central-monitoring.sh registry vm-assign VM_UUID cust-b
sudo ./tools/setup-central-monitoring.sh registry vm-at VM_UUID --at 1795500000
sudo ./tools/setup-central-monitoring.sh registry vm-rename VM_UUID new-name
sudo ./tools/setup-central-monitoring.sh registry vm-unassign VM_UUID
sudo ./tools/setup-central-monitoring.sh registry vm-retire VM_UUID
sudo ./tools/setup-central-monitoring.sh registry tenant-disable cust-a
sudo ./tools/setup-central-monitoring.sh registry tenant-enable cust-a
```

`vm-add` defaults to reserved `internal` (operator-only). Disable requires unassigning all active registry VMs. Retirement closes the interval and prohibits UUID reuse. Rename only changes display metadata. Move records placement, not an actual libvirt migration. These operations do not change metrics, credentials or ownership of historical data in storage.

Registration, transfer, move and retirement accept `--at UNIX_SECONDS` (UTC, no future times; default central clock). Intervals are half-open `[start,end)` and transitions must be strictly later than the previous start. Wait until the next second for successive transitions. Use explicit past times only with verified records. `vm-at` returns the recorded interval or JSON null, not authorization. Host Inventory and Registry are separate: coordinate host removal and VM moves manually. Historical host IDs are preserved even after inventory removal.

The registry file `/etc/kvm-vm/central/registry/ownership.json` (schema v1, mode 0600) is created on first mutation; directory mode 0700. Writes use a separate flock file, atomic replacement and fsync. Do not edit the registry or remove the lock during operations. Unsupported/corrupt schema fails closed. `up`, `down`, `sync-templates`, `reload` do not overwrite it. Include this directory in protected central configuration backups; pause management mutations during backup/restore.

Future isolation requires trusted Host credentials, VM ownership verification at sample time (including queue replay), isolated tenant storage/query access and exclusion of host-level and unassigned metrics. No routing, customer API, RBAC or credential changes are implemented.
