# Two-host full Backup acceptance exercise

This is an operator-run CLI/SSH acceptance workflow, not a mock test. Record
every command, exit status and assertion in the ticket evidence. Do not mark
the exercise passed if a host, tool, transfer authorization or check is missing.
Use the [full recovery procedure](full-backup-restore.md) for capture and Restore.

## Prepare a disposable source guest

For the dedicated Debian test host, `sudo python3 tests/prepare_full_backup.py`
automates the source CLI/SSH marker exercise, captures management files and a
full backup, verifies checksums, and leaves the unique test guest shut off with
autostart disabled. It requires the existing E2E dependencies including pytest
and PyYAML, a cached/downloadable Debian 12 image and the `default` NAT network.
It deliberately retains the printed test root and guest key. Run it only during
the exclusive test window; inspect its backup log and topology before accepting
the set. It does not transfer anything or count as destination acceptance. If it
fails, inspect its printed resources and fence the test guest before retrying or
starting a destination. The steps below describe the full operator workflow.

On the tested Debian 13 host, after checking the per-domain AppArmor runtime
directory pattern, use:

```bash
sudo python3 tests/prepare_full_backup.py --socket-template \
  '/var/lib/libvirt/qemu/domain-{id}-{name20}/recovery-nbd.sock'
```

This host-specific pattern is not a portable libvirt path contract. The test
substitutes the newly created guest's runtime ID and first 20 name characters;
the directory must already exist and permit that guest's socket creation.

1. Allocate a unique `e2e-full-<UTC>-<random>` name, a dedicated guest SSH key
   and a recovery-set directory. Record both hosts' machine identities and
   versions. The destination must be a different compatible KVM host.
2. Generate a YAML using the Debian 12 example or `build_vm_yaml` in
   `tests/test_e2e.py`: 1 vCPU, 1024 MiB, 10 GiB standalone disk, NAT DHCP, user
   `admin`, and the dedicated public key. Use independent test artifact roots
   via kvm-vm's environment overrides, retaining those exact roots in the notes.
   Run `kvm-vm validate FILE`, then `kvm-vm create FILE` through the CLI.
3. Discover the lease with `virsh domifaddr NAME --source lease`. Connect over
   SSH and wait for `sudo cloud-init status --wait` to finish successfully.
   Record the guest SSH host-key fingerprints through the trusted test console
   or authenticated source connection before pinning them for target verification.
4. Through guest SSH, create a unique marker and capture expected identity:

   ```bash
   printf '%s\n' 'REPLACE-WITH-UNIQUE-DRILL-ID' | sudo tee /var/tmp/recovery-marker
   sync
   sha256sum /var/tmp/recovery-marker
   cat /etc/machine-id
   sudo cat /var/lib/cloud/data/instance-id
   sudo sha256sum /etc/ssh/ssh_host_*_key.pub
   hostname
   ```

   Save this output into the recovery set before sealing. Also record the
   source `virsh domuuid NAME`, interface MAC, state `instance_id`, and cloud
   directory hashes. A guest filesystem freeze does not replace an application
   recovery check; the initial drill has only the synced marker workload.
5. Follow the complete capture procedure with `-l full`. Check that the guest
   remains running and SSH responds afterward. Record freeze warnings honestly;
   the initial marker workload can test crash recovery but cannot certify a
   database. Verify the backup and seal the entire set.

## Remove source dependence

1. Shut the test source down gracefully and wait for `shut off`. Disable its
   autostart. Do not destroy production guests or remove source files to
   simulate failure. Keep the source test guest off throughout target boot.
2. Transfer only the sealed recovery set to authorized independent storage or
   the destination, then verify SHA-256 and external-tool integrity there.
   Supply the dedicated guest test key separately through the operator's secure
   channel; never transfer the source host's administrative private key.
3. Run the destination phase from its own SSH session with no agent forwarding,
   source credentials or source filesystem mounts. Record this isolation and
   the different host identity. All recovery commands must use only the set,
   installed software and prepared target host resources.

## Destination assertions

1. Follow the destination procedure, logging every path mapping and preserving
   identity. Define and boot only after confirming no identity conflict.
2. Verify SSH using the previously captured host keys, then rerun the guest
   commands above. Assert identical marker hash, machine ID, cloud-init instance
   ID, hostname and all recorded SSH key fingerprints. Check cloud-init did not
   create a new instance or rerun first-boot provisioning. Compare the domain
   UUID/MAC and management instance ID independently.
3. Run `kvm-vm status NAME`, `kvm-vm list --managed` and
   `kvm-vm validate INSTALLED-YAML` using the target environment. Assert the VM
   is managed, paths exist locally, and actual disk/seed paths match the JSON.
   Shut down/start the recovered guest with virsh and repeat the marker and
   identity checks to establish persistent bootability.
4. For this disposable guest only, after retaining all recovery evidence and
   with no new backup checkpoint, shut down and run
   `kvm-vm delete NAME --keep-storage --yes`. Assert the domain is undefined
   while disk, cloud files, effective YAML and JSON remain byte-for-byte intact.
   Redefine the saved destination XML and confirm `status` is managed again.
   This tests management beyond a display-only listing without reinstalling the
   recovered data. If any recovery metadata remains, expect safe refusal and
   investigate; do not erase metadata just to force a passing result.
5. Record elapsed backup/restore/boot times and the full assertion results in
   `docs/plans/20261018_vm-recovery/ticket-03-validation.md`. A source-only or
   same-host success remains partial. Do not advance to ticket 04 yet.

## Cleanup

After acceptance evidence is retained, clean only the recorded test resources.
Check the exact domain identity and disk paths first. Full backup creates source
checkpoint metadata: retire that disposable baseline deliberately using the
external tool/libvirt procedure before attempting kvm-vm deletion. Do not add
blanket checkpoint deletion to kvm-vm or operate on other guests. Retain the
sealed independent set until the operator agrees the drill is no longer needed.
