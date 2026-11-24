# 03 — Tenant and VM ownership registry

Separate versioned central registry from expected-host inventory. Add tenant and VM registration, status, assignment/unassignment, placement changes and retirement; preserve intervals. Validate references and IDs, serialize concurrent updates, preserve data on deployment and rollback. No customer queries or storage routing. Test shared host, migration, ownership transfer, unknown resources, malformed data, concurrent writes, and readiness gate.
