English | [繁體中文](templates.zh-TW.md)

# VM definition templates

Templates are ordinary VM YAML definitions, not a template language. They make
common starting configurations available after installation without the checkout.
Review the effective settings before creating a VM.

## Discover and use

```sh
kvm-vm template list
kvm-vm template show debian13-bridge
sudo kvm-vm validate @ubuntu24-nat
sudo kvm-vm create --template ubuntu24-nat --dry-run
sudo kvm-vm create @ubuntu24-nat
sudo kvm-vm validate @clone-target-nat --clone
sudo kvm-vm clone nat01 @clone-target-nat --dry-run
```

`@NAME` and `--template NAME` are equivalent for `validate`, `create` and
`clone`. The `.yaml` suffix is optional. Use exactly one definition source.
An ordinary path is always an ordinary path: `./my-vm.yaml`, `/srv/vm.yaml`,
`~/vm.yaml`, and even `./@literal.yaml` work as before. Missing ordinary paths
never fall back to a template. `~NAME` is not a template syntax.

`template list` shows each effective name once, its source (`local` or
`builtin`) and a descriptive type (`create` has an image source, `clone` has
none, `invalid` could not be parsed). This is not full definition validation;
run `validate` with `--clone` where appropriate. `template show` prints the
selected raw YAML. These commands need no running libvirt service. Template
resolution reports the selected source/path on stderr. `create` still performs
validation and host pre-flight checks, then prompts unless `--yes` is supplied.

## Directories and upgrades

| Directory | Role |
|---|---|
| `/etc/kvm-vm/templates/` | Local administrator templates; first lookup priority; never overwritten by the installer |
| `/usr/local/share/kvm-vm/templates/` | Built-in templates; installed/updated from top-level `examples/*.yaml` |
| `/etc/kvm-vm/definitions/` | Effective definitions of managed VMs, not templates |

`install.sh` creates the directories and installs built-in YAML files with mode
0644. Re-running it updates the shipped files, preserving local templates and
existing VM definitions. It does not remove extra vendor files automatically.
Use the local directory for customization rather than editing vendor files.
For example, with the default installation paths:

```sh
sudo install -m 0644 /usr/local/share/kvm-vm/templates/ubuntu24-nat.yaml \
  /etc/kvm-vm/templates/hosting-standard.yaml
sudoedit /etc/kvm-vm/templates/hosting-standard.yaml
sudo kvm-vm validate @hosting-standard
```

A local template with the same filename shadows a built-in template completely,
not as a YAML merge. Invalid or unsafe local templates fail closed, rather than
falling back. Template names are single basenames (letters, digits, `_`, `-`,
`.`), starting with a letter/digit; traversal, slashes, symlinks and writable
untrusted directory/file entries are rejected. For root operations keep the
absolute template directories, their ancestors and YAML files root-owned and not
writable by group/others. A root-owned sticky ancestor such as `/tmp` is allowed
for protected subdirectories. Template YAML is trusted administrator input;
it can include guest commands, image paths and SSH keys. Treat it accordingly.

The installer honors `PREFIX` (default `/usr/local`) for vendor files. Normal
`PREFIX/sbin` or `PREFIX/bin` installations infer that prefix at runtime. With
an arbitrary `DEST` outside those directories, set
`KVM_VM_BUILTIN_TEMPLATE_DIR` explicitly for both installation and use. The two
template directories can be independently overridden through
`KVM_VM_TEMPLATE_DIR` and `KVM_VM_BUILTIN_TEMPLATE_DIR` (absolute paths).
`KVM_VM_DEF_DIR` remains independent; template directories must be distinct from
one another and the effective definition directory. From a checkout without
installing, use a normal YAML path or explicitly set the built-in directory.

## Choose a starting template

| Distribution | NAT (DHCP) | Bridge (DHCP) | Macvtap (DHCP) |
|---|---|---|---|
| Ubuntu 24.04 | `ubuntu24-nat` | `ubuntu24-bridge` | `ubuntu24-macvtap` |
| Ubuntu 26.04 | `ubuntu26-nat` | `ubuntu26-bridge` | — |
| Debian 13 | `debian13-nat` | `debian13-bridge` | `debian13-macvtap` |
| Debian 12 | `debian12-nat` | — | — |
| Rocky Linux 9 | `rocky9-nat` | `rocky9-bridge` | — |
| Rocky Linux 10 | `rocky10-nat` | — | — |
| AlmaLinux 9 | `almalinux9-nat` | `almalinux9-bridge` | — |
| AlmaLinux 10 | `almalinux10-nat` | — | — |

The original `ubuntu24-web01` (bridge/static IP), `debian13-dhcp` (bridge/DHCP)
and `clone-target` (bridge/static IP) remain available for compatibility.
`clone-target-nat` is a DHCP clone target. Clone templates omit the base image;
use `validate --clone` and `clone`, not `create`.

New create templates use 2 vCPUs, 4 GiB memory, 40 GiB disk, q35, auto MAC,
`admin` with an SSH public key, QGA and the balanced maintenance profile. They
are examples, not host discovery. NAT needs an active `default` libvirt network;
bridge needs `br0` (or your bridge); Macvtap needs a real Ethernet parent (`eno1`
is a placeholder) and normally cannot communicate directly with the host. See
[Networking](networking.md). The static-IP examples must be customized before
use on another LAN; the host pre-flight cannot guarantee IP uniqueness.

Use the existing [environment overrides](configuration.md#environment-overrides)
for names, addresses and sizing without editing a template:

```sh
sudo KVM_VM_NAME=web03 KVM_VM_DISK=80 kvm-vm create @debian13-nat
sudo KVM_VM_NAME=web04 KVM_VM_MACVTAP=enp2s0 kvm-vm create @ubuntu24-macvtap --dry-run
```

Fixed names are not automatically incremented. Choose a unique name and network
settings on every repeat. Template variables, inheritance and new CLI override
flags are intentionally not introduced. For more customized settings, edit a
local copy of the YAML.

For named templates, relative `file:` SSH key references and local image paths
are resolved from the template directory. Existing tilde handling for SSH keys
is unchanged. The effective definition saves resolved public keys and absolute
local image paths, so a later template edit/removal does not change a VM's saved
configuration. Reinstall uses the saved effective definition; external image
files must still be available when required. State records the chosen template
path in `definition_source`. Creation never modifies the template itself.

Monitoring examples are separate under [`examples/monitor/`](../examples/monitor/)
and are not installed as VM templates.
