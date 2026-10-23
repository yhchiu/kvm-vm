English | [繁體中文](access.zh-TW.md)

# Access and security

- [Default policy](#default-policy)
- [Connect with SSH](#connect-with-ssh)
- [Console password login](#console-password-login)
- [Regain access with a new SSH key](#regain-access-with-a-new-ssh-key)

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
- There is no command to recover a lost password. Use another access path or the
  offline method below.

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

## Regain access with a new SSH key

If you lost the private key and have no console password, add a new public key to
the disk while the VM is off:

1. Shut the VM down and wait until it is off:

   ```bash
   sudo virsh shutdown web01
   sudo virsh domstate web01      # wait for "shut off"
   ```

2. Add the new key with `virt-customize`:

   ```bash
   sudo virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
   ```

3. Start the VM and connect with the new key:

   ```bash
   sudo virsh start web01
   ssh admin@<vm-ip> -i /path/to/new_key
   ```

Never edit the disk of a running VM.
