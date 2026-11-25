English | [繁體中文](access.zh-TW.md)

# Access and security

- [Default policy](#default-policy)
- [Connect with SSH](#connect-with-ssh)
- [Console password login](#console-password-login)
- [Online password reset (QGA)](#online-password-reset-qga)
- [Regain access](#regain-access)

## Default policy

Every VM starts with these cloud-init settings:

- The management user (`cloud_init.user`, default `admin`) has a **locked
  password** (`lock_passwd: true`) and passwordless `sudo`.
- SSH accepts **keys only** (`ssh_pwauth: false`).
- root cannot log in over SSH (`disable_root: true`). This setting controls SSH
  only; it does not set a root password policy. Custom images keep their own
  root account settings.

No root password and no automatic console login are set. To plan for console
access, use [console password login](#console-password-login); do not put
passwords in the YAML or in `runcmd`.

## Connect with SSH

Find the address, then connect as the management user with the matching private
key:

```bash
sudo kvm-vm list --ips
ssh admin@<vm-ip> -i ~/.ssh/id_ed25519
ssh -p 2222 admin@<vm-ip> -i ~/.ssh/id_ed25519   # when cloud_init.ssh_port is 2222
```

Macvtap VMs cannot be reached from their own host. Connect from another machine,
or use the console. See [Macvtap limitations](networking.md#macvtap-limitations).

## Console password login

`kvm-vm console` attaches to the serial console, but the password is locked, so
you cannot log in there by default. Console password login gives the management
user a random password. It works with every network mode and is the way to
manage a Macvtap VM from its own host. SSH stays key-only.

The host needs OpenSSL, and the credential file is written with POSIX ownership
and permissions, so the host must be Linux.

### Turn it on

Merge these fields into the `cloud_init` section of the definition. Keep the SSH
keys; they are still required.

```yaml
cloud_init:
  user: admin
  console_password: true
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
```

Create the VM with a new credential file in a private directory:

```bash
sudo install -d -m 0700 /root/vm-credentials
sudo kvm-vm create web01.yaml --console-password-file /root/vm-credentials/web01.json
sudo kvm-vm console web01
```

At the login prompt, use the `user` and `password` from the file.

### The credential file

- The path must be a **new** file in a directory that belongs to the
  administrator running kvm-vm and that no one else can access. Existing files
  and symbolic links are refused.
- The file has mode 0600 and holds JSON with `vm`, `user` and `password`. The
  password is 24 random characters.
- The password is never printed and never saved in kvm-vm definitions or state.
  Only a salted hash goes into the cloud-init files, which have restricted
  permissions.
- When the definition has `console_password: true`, `create`, `clone` and
  `reinstall` require `--console-password-file`, even with `--dry-run`. A dry run
  checks the path but does not create the file. `validate` never creates a
  password. Giving the option when the setting is off is an error.

### What happens in the guest

The password stays locked until the last first-boot step. That step turns off
SSH password and keyboard-interactive login, checks the SSH configuration,
starts the serial console login service, and only then unlocks the password.
SSH key login keeps working.

`create` returning successfully does not prove this step worked. Check cloud-init
and try the console login.

### Risks and responsibilities

- The password is not limited to the serial console. Anyone who has it gets
  administrator rights through the user's passwordless `sudo`.
- It never expires, and the user is not asked to change it.
- Protect the file. Keep it even if `create` fails later, because the VM's files
  may already use it. A retry needs a new file path.
- `kvm-vm delete` does not delete the file. Remove it yourself.
- If someone changes the password in the guest, the file is out of date.
- Lost plaintext passwords cannot be recovered. Use [online reset](#online-password-reset-qga)
  or an [offline method](#regain-access) below.

### Clones and reinstalls

`clone` uses the **target** definition's setting. When it is on, give a new file:

```bash
sudo kvm-vm clone web01 web02.yaml --console-password-file /root/vm-credentials/web02.json
```

Each clone gets its own password. Before the clone first boots, kvm-vm locks the
management user's password on the copied disk, including a password the source
VM had. Only a clone with the setting on is unlocked, with its new password. The
source VM is not changed. If writing the credential fails, no guest files are
touched; if a later step fails, the clone's files and the credential are kept.

`reinstall` keeps the saved setting. When it is on, give a new file; the VM gets
a new password and the old one stops working:

```bash
sudo kvm-vm reinstall web01 --yes --console-password-file /root/vm-credentials/web01-reinstalled.json
```

The new credential is written before the VM is shut down or the disk is replaced.
Existing credential files are never overwritten or removed. A VM with the setting
off, or created before this feature existed, stays locked and needs no file.
`--dry-run` creates no credential.

### Tested guests

Console login, `sudo`, key-only SSH and reboot passed on Debian 12 and 13,
Ubuntu 24.04 and 26.04, Rocky Linux 8 and AlmaLinux 8, together with locked
defaults in all three network modes, clone credential isolation and reinstall
password changes. Rocky Linux and AlmaLinux 9 and 10 have not been tested for
console login yet. See the
[console validation report](plans/20261019_macvtap-console/ticket-06-validation.md).

## Online password reset (QGA)

Reset a running managed Linux VM without SSH access or a reboot. The password
is generated by kvm-vm; specifying your own password or another account is not
supported. The configured `cloud_init.user` must be a non-root local login
account (UID at least 1000). QGA must support both the native password API and
`guest-exec` for pre-flight checks. No additional libvirt Python package is needed.

```bash
sudo install -d -m 0700 /root/vm-credentials
sudo kvm-vm reset-password web01 --password-file /root/vm-credentials/web01-reset.json --dry-run
sudo kvm-vm reset-password web01 --password-file /root/vm-credentials/web01-reset.json
# automation: append --yes
# if console_password is false, explicitly opt in:
sudo kvm-vm reset-password web01 --enable-console --password-file /root/vm-credentials/web01-enabled.json --yes
```

The pre-flight checks confirm the managed identity (name, MAC and libvirt UUID),
running state, domain job, incomplete snapshot record, cloud-init readiness,
QGA capability, local account, console and SSH policy, and private credential
path. The command takes the lifecycle lock, including in dry-run. Confirmation
is `[y/N]`; unattended use requires `--yes`. A completed Snapshot or Backup
checkpoint does not itself prohibit reset, but an active domain job or incomplete
managed Snapshot does. Coordinate separately with external backup/lifecycle tools.

`--enable-console` explicitly permits password unlocking and enables the serial
getty if necessary. Without it, the saved console policy must already be true
and the serial getty active. The command checks standard on-disk SSH configuration
(including Include files and Match directives) and `sshd -T`; it conservatively
rejects even an explicitly enabled password/keyboard-interactive directive that
another setting currently overrides. Correct and reload SSH policy separately
before retrying. It does not rewrite or restart sshd, change authorized keys,
root credentials, account expiration policy, or sudo/PAM configuration. A custom
sshd invocation, unreloaded configuration, alternate SSH server or other PAM
service may still accept passwords; confirm the running configuration and other
login paths before enabling a password. The password grants administrative
privileges via passwordless sudo. Reset is not a way to enable SSH password login.

A private, new `0600` JSON file (`vm`, `user`, `password`) is written and synced
before mutation. The generated password is 24 characters; a salted SHA-512 crypt
hash is passed only via a pipe to `virsh set-user-password ... --encrypted`,
not argv, environment, or a shell command. Client debug logging and history are
suppressed; stdout/stderr are captured and never echoed. Do not enable sensitive
libvirt/QGA debug tracing, RPC inspection or process dumps on the trusted host or
agent; privileged instrumentation may capture the hash. `--timeout` (default 30,
1–300 seconds) bounds this virsh call, not the entire pre-flight workflow.

The command requires virsh's positive C-locale acknowledgement as well as a zero
exit status; stdin mode alone can return zero despite a subcommand failure. If
QGA times out, the connection fails, or the acknowledgement is missing, retain
the credential and verify console login before retrying with a **new** path. A
failed/uncertain operation may have changed the password, or enabled the getty
without changing the password. There is no automatic retry or offline fallback.
The generated credential may not be valid until the operation is confirmed and
login tested. A successful `--enable-console` updates only the effective YAML,
not the original definition or old seed. If persistence fails after the reset,
retain the credential, inspect Guest access and reconcile the saved policy.

Reboot/migration retain the guest password. Clone locks the source management
account even when its saved console policy was false, and uses the target policy.
Reinstall with console login enabled requires a fresh `--console-password-file`.
Snapshot rollback and Backup restore recover the password from their recovery
point; rerunning old cloud-init seed data may also restore the old login policy.
Keep credential files private and out of source control; they are not automatically
deleted. For a shut-off VM or a broken Guest Agent, use the explicit offline
recovery procedure below rather than shutting down a production VM automatically.

## Regain access

If the VM has no usable console password, change its disk offline with
`virt-customize`. Pick the method that matches the problem:

- [Set the management user's password](#set-the-management-users-password) when
  the VM cannot be reached over the network, for example a Macvtap VM managed
  from its own host, or a guest whose network settings are broken. You then log
  in on the console.
- [Add a new SSH key](#add-a-new-ssh-key) when the network works but the private
  key is lost.

`virt-customize` must not run against a running VM. Shut the VM down first and
wait until it is off:

```bash
sudo virsh shutdown web01
sudo virsh domstate web01      # wait for "shut off"
```

If the VM has a kvm-vm [Snapshot](snapshots.md), use `-d web01`, which edits the
disk the VM currently uses. Do not use `-a .../web01.qcow2`: that file is the
Snapshot's read-only recovery point.

### Set the management user's password

This gives the management user (`cloud_init.user`, default `admin`) a password.
It is the same account that [console password login](#console-password-login)
uses, and no root password is set. The account name is in the VM's effective
definition, `/etc/kvm-vm/definitions/web01.yaml`.

**From standard input**, for a VM defined in libvirt. The password is not shown
while you type, and it stays out of shell history and the process list:

```bash
read -rsp 'New password for admin: ' pw && echo
printf '%s\n' "$pw" | sudo virt-customize -d web01 --password admin:file:/dev/stdin
unset pw
```

**From a file, by disk path**, for a disk that has no libvirt domain, such as one
kept with `delete --keep-storage` or restored from a Backup. The first line of
the file is the password. Create it with mode 0600 and delete it afterwards:

```bash
sudo virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 \
  --password admin:file:/root/web01-admin-password
sudo rm /root/web01-admin-password
```

Then start the VM and log in as `admin` on the console. The account has
passwordless `sudo`:

```bash
sudo virsh start web01
sudo kvm-vm console web01
```

- kvm-vm turns off SSH password login (`ssh_pwauth: false`). It turns off
  keyboard-interactive login only for console password login, so on other VMs
  that depends on the image. Check it in the guest; both values should be `no`:

  ```bash
  sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication) '
  ```

  If either is `yes`, set it to `no` in the SSH configuration and restart SSH,
  or lock the password again as soon as you are done.
- On SELinux guests such as Rocky Linux and AlmaLinux, current `virt-customize`
  versions fix the SELinux labels of changed files automatically. Older versions
  need `--selinux-relabel`; on current versions that option does nothing, so it
  is safe to add.
- After you have fixed the problem, lock the password again with
  `sudo passwd -l admin` in the guest to return to the default policy.
- kvm-vm's acceptance tests cover console password login, not this procedure.

### Add a new SSH key

```bash
sudo virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
sudo virsh start web01
ssh admin@<vm-ip> -i /path/to/new_key
```
