[English](configuration.md) | 繁體中文

# VM 定義檔參考

VM 定義檔是描述一台 VM 的 YAML 檔。`validate`、`create`、`clone` 會讀取它並補上預設值；`create` 與 `clone` 會把結果存成該 VM 的**生效定義檔**（effective definition），位置為 `/etc/kvm-vm/definitions/NAME.yaml`。

- [最小定義檔](#最小定義檔)
- [完整範例](#完整範例)
- [欄位參考](#欄位參考)
- [作業系統映像檔](#作業系統映像檔)
- [SSH 金鑰與連接埠](#ssh-金鑰與連接埠)
- [環境變數覆寫](#環境變數覆寫)
- [宿主機路徑](#宿主機路徑)
- [用 Git 管理定義檔](#用-git-管理定義檔)

網路欄位請見[網路設定](networking.zh-TW.md)。

## 最小定義檔

只有三項是必填：VM 名稱、映像檔（`create` 需要），以及至少一把 SSH 公鑰。

```yaml
vm:
  name: test01
storage:
  image:
    distro: debian13
cloud_init:
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
```

其餘欄位皆有預設值：2 個 vCPU、2048 MiB 記憶體、20 GiB 磁碟、使用者 `admin`，並透過宿主機的 `br0` 橋接以 DHCP 取得位址。要查看補齊預設值後的完整結果：

```bash
kvm-vm validate test01.yaml
```

`validate` 為離線檢查，不需要 root、libvirt 或執行中的服務。

## 完整範例

以下是 [`examples/ubuntu24-web01.yaml`](../examples/ubuntu24-web01.yaml)，一台在 `br0` 橋接上使用靜態位址的 VM：

```yaml
version: 1

vm:
  name: web01
  vcpus: 4
  memory_mib: 8192
  cpu: host-passthrough
  autostart: true
  start: true
  os_variant: ubuntu24.04

storage:
  disk_gib: 80
  bus: virtio
  cache: none
  discard: unmap
  image:
    distro: ubuntu24.04

network:
  mode: bridge
  bridge: br0
  model: virtio
  mac: auto
  ipv4:
    method: static
    address: 192.168.10.51/24
    gateway: 192.168.10.1
    dns:
      - 192.168.10.1
      - 1.1.1.1
  ipv6:
    method: disabled

cloud_init:
  user: admin
  console_password: false
  ssh_port: 22
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
  package_update: false
  qemu_guest_agent: true
  timezone: Asia/Taipei
  packages:
    - curl
    - vim
  runcmd: []
```

其他範例：[`debian13-dhcp.yaml`](../examples/debian13-dhcp.yaml)（橋接＋DHCP）、[`ubuntu24-nat.yaml`](../examples/ubuntu24-nat.yaml)（libvirt NAT）、[`clone-target.yaml`](../examples/clone-target.yaml)（`clone` 的目標）。

## 欄位參考

`version` 可省略，若填寫必須為 `1`。

### `vm`

| 欄位 | 預設值 | 說明 |
|---|---|---|
| `name` | 必填 | libvirt domain 名稱，也是 guest 的主機名稱。可用英數字、`.`、`_`、`-`，最長 63 字元，須以英數字開頭。 |
| `vcpus` | `2` | 1 到 1024。 |
| `memory_mib` | `2048` | 至少 256。 |
| `cpu` | `host-passthrough` | 傳給 `virt-install --cpu`。詳見 [CPU 模型](#cpu-模型)。 |
| `autostart` | `true` | 宿主機開機時自動啟動 VM。 |
| `start` | `true` | `create` 或 `clone` 後開機。`--no-start` 可在單次執行時覆寫。 |
| `os_variant` | 依映像檔別名選擇，否則為 `generic` | `virt-install` 使用的 libosinfo 名稱。設定後會取代別名的選擇。詳見 [os_variant](#os_variant)。 |

### `storage`

| 欄位 | 預設值 | 說明 |
|---|---|---|
| `disk_gib` | `create` 為 `20`；`clone` 沿用來源大小 | 系統磁碟大小（GiB），不可小於映像檔（`clone` 時不可小於來源磁碟）。 |
| `bus` | `virtio` | |
| `cache` | `none` | |
| `discard` | `unmap` | |
| `image.distro` | | [內建別名](#內建別名)。 |
| `image.url` | | 下載雲端映像檔。 |
| `image.path` | | 使用本機雲端映像檔。 |
| `image.sha256` | 無 | 選填的映像檔檢查碼，不符時指令會停止。 |

`create` 必須在 `image.distro`、`image.url`、`image.path` 中恰好指定一項。`clone` 會忽略 `image`，因為來源 VM 的磁碟就是映像檔。

每台 VM 都有一份獨立且完整的 qcow2 磁碟。更新或刪除快取的映像檔，絕不會影響既有 VM。

### `network`

| 欄位 | 預設值 | 說明 |
|---|---|---|
| `mode` | `bridge` | `bridge`、`nat` 或 `macvtap`。詳見[網路設定](networking.zh-TW.md)。 |
| `bridge` | `br0` | 僅限橋接模式。 |
| `libvirt_network` | `default` | 僅限 NAT 模式。 |
| `parent` | Macvtap 必填 | 僅限 Macvtap 模式：宿主機的實體介面，例如 `eno1`。 |
| `model` | `virtio` | |
| `mac` | `auto` | `auto` 會挑選未使用的 `52:54:00:xx:xx:xx` 位址，並存入生效定義檔。 |
| `ipv4.method` | `dhcp` | `dhcp`、`static` 或 `disabled`。 |
| `ipv4.address` | | 僅限靜態，需含前綴長度，例如 `192.168.10.51/24`。 |
| `ipv4.gateway` | | 靜態時必填。 |
| `ipv4.dns` | | DNS 伺服器位址清單。 |
| `ipv6.method` | `disabled` | 也接受 `dhcp` 與 `static`（搭配 `address`、`gateway`）。IPv6 值會不經檢查直接交給 cloud-init。 |

cloud-init 以 MAC 位址找出 guest 的網卡並改名為 `eth0`，因此無論映像檔原本把網卡命名為 `ens3`、`enp1s0` 或其他名稱，定義檔都一樣適用。

### `cloud_init`

| 欄位 | 預設值 | 說明 |
|---|---|---|
| `user` | `admin` | 非 root 的管理帳號，具免密碼 `sudo` 權限。 |
| `ssh_authorized_keys` | 必填 | 至少一把金鑰。詳見 [SSH 金鑰](#ssh-金鑰)。 |
| `ssh_port` | `22` | 1 到 65535。詳見 [SSH 連接埠](#ssh-連接埠)。 |
| `console_password` | `false` | `true` 會產生隨機 Console 密碼。詳見 [Console 密碼登入](access.zh-TW.md#console-密碼登入)。 |
| `qemu_guest_agent` | `true` | 安裝並啟動 `qemu-guest-agent`，並加入 virtio 通道。某些網路模式下 `list --ips` 需要它，快照凍結檔案系統也需要它。 |
| `package_update` | `false` | 首次開機時更新套件索引（cloud-init 的 `package_update`）。 |
| `packages` | `[]` | 首次開機時安裝的套件。 |
| `timezone` | 映像檔預設 | 例如 `Asia/Taipei`。 |
| `runcmd` | `[]` | cloud-init 的 `runcmd` 項目，會在 kvm-vm 自己的首次開機步驟（SSH 連接埠、Console 密碼）之前執行。 |

管理帳號的密碼預設鎖定，SSH 僅接受金鑰。詳見[存取與安全](access.zh-TW.md)。

### CPU 模型

`cpu: host-passthrough` 讓 guest 使用宿主機 CPU 的全部功能，適合單一主機或硬體相同的多台主機。若打算在不同世代 CPU 之間即時遷移，請改用可遷移的 CPU 模型。

實體 CPU 沒有的指令，guest 也無法使用。Rocky Linux 與 AlmaLinux 9 需要 x86-64-v2 CPU，其 10 版映像檔需要 x86-64-v3。在較舊的 CPU 上，這些 guest 可以建立，但無法完成開機。

## 作業系統映像檔

### 內建別名

| 別名 | 依序嘗試的 `os_variant` | 建立與 SSH 已驗證 |
|---|---|---|
| `ubuntu24.04` | `ubuntu24.04` | 是 |
| `ubuntu26.04` | `ubuntu26.04`, `ubuntu24.04` | 是 |
| `debian12` | `debian12` | 是 |
| `debian13` | `debian13`, `debian12` | 是 |
| `rocky8` | `rocky8` | 是 |
| `rocky9` | `rocky9`, `rocky8` | 是（x86-64-v2 主機） |
| `rocky10` | `rocky10`, `rocky9` | 尚未：需要 x86-64-v3 主機 |
| `almalinux8` | `almalinux8` | 是 |
| `almalinux9` | `almalinux9`, `almalinux8` | 是（x86-64-v2 主機） |
| `almalinux10` | `almalinux10`, `almalinux9` | 尚未：需要 x86-64-v3 主機 |

別名下載的是發行版持續更新的 `current` 或 `latest` 雲端映像檔，因此每次下載的內容可能不同。實測紀錄與使用的映像檔請見 [tests/REAL_MACHINE_RESULTS.md](../tests/REAL_MACHINE_RESULTS.md)（英文）。

### 自訂映像檔

任何支援 cloud-init 的 qcow2 或 raw 映像檔都能直接使用，不需修改程式：

```yaml
storage:
  image:
    url: https://images.example.com/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

或

```yaml
storage:
  image:
    path: /srv/images/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

要讓正式環境的建置可重現，固定的 URL 或本機檔案加上 `sha256`，比會變動的別名更理想。

### 映像檔快取與 `--refresh-image`

下載的映像檔會快取在 `/var/lib/libvirt/images/base/`，之後的 `create` 與 `reinstall` 會重複使用。加上 `--refresh-image` 可重新下載：

```bash
sudo kvm-vm create newvm.yaml --refresh-image
```

- 一定會重新下載：不會比對 `ETag` 或 `Last-Modified`。
- 不影響既有 VM，因為每台 VM 都有自己的磁碟。
- `image.url` 的映像檔以 URL 結尾的檔名快取。結尾檔名相同的兩個 URL 會共用同一個快取檔。
- `image.path` 的映像檔直接使用原檔，不會快取。

### `os_variant`

`virt-install` 依 `os_variant` 選擇最佳化的虛擬硬體預設值。kvm-vm 在定義 VM 時依以下步驟選擇：

1. 有設定 `vm.os_variant` 就從它開始，否則從映像檔別名開始。
2. 若該名稱是內建別名，依[上方表格](#內建別名)列出的順序嘗試：先試該版本本身，再試前一版。其他名稱則照原樣嘗試。
3. 使用宿主機 `osinfo-query os` 列出的第一個 variant。若都沒有列出，或未安裝 `osinfo-query`，則使用 `generic`。任何退回都會記錄警告。

宿主機的 libosinfo 可能比新版發行版舊。例如 Debian 13 的 osinfo-db（0.20250606）認得 `almalinux10`，但不認得 `rocky10` 與 `ubuntu26.04`，因此在該主機上 `rocky10` 會使用 `rocky9`，`ubuntu26.04` 會使用 `ubuntu24.04`。即使用 `generic`，VM 仍可正常開機，因為 kvm-vm 一律使用 virtio 磁碟與網卡及序列主控台。

`image.distro`、`KVM_VM_OS` 與 `reinstall --os` 都依相同步驟，因此在同一台宿主機上，同一個別名一定得到相同的 variant。使用自訂映像檔時，請自行設定 `vm.os_variant`。

### 新增別名

若要使用 `distro: myos1` 這類簡短名稱，請在 `kvm-vm` 腳本的 `DISTROS` 加入項目，再重新執行 `sudo ./install.sh`：

```python
DISTROS = {
    ...
    "myos1": {
        "url": "https://images.example.com/myos-1-cloud.x86_64.qcow2",
        "cache": "myos-1-cloud.x86_64.qcow2",  # 映像檔快取中的檔名
        "os_variants": ["myos1"],              # 依序嘗試的 libosinfo ID
    },
}
```

請依 `osinfo-query os` 顯示的 ID 填寫，新版在前。kvm-vm 會使用宿主機認得的第一個，否則使用 `generic`。

請把這類修改保留在自己的分支，因為安裝新版 kvm-vm 會覆蓋腳本。若之後要 `clone` 這些 VM，請確認宿主機的 `virt-sysprep` 支援該 guest，詳見[檢查 virt-sysprep 支援](commands.zh-TW.md#檢查-virt-sysprep-支援)。

## SSH 金鑰與連接埠

### SSH 金鑰

`ssh_authorized_keys` 的每一項可以是完整的 OpenSSH 公鑰，或是 `file:` 加上 `.pub` 檔路徑：

```yaml
cloud_init:
  ssh_authorized_keys:
    - ssh-ed25519 AAAAC3Nza... me@laptop
    - file:~/.ssh/id_ed25519.pub
    - file:keys/ops.pub
```

- 相對的 `file:` 路徑以 YAML 檔所在目錄為基準。
- `~` 是你的家目錄。透過 `sudo` 執行時，是執行 `sudo` 的使用者（`$SUDO_USER`）的家目錄，而不是 root 的。

### SSH 連接埠

`ssh_port` 設定 guest SSH 服務的監聽埠。使用預設的 `22` 時，kvm-vm 不會更動映像檔的 SSH 設定。

設為其他連接埠（例如 `ssh_port: 2222`）時，kvm-vm 會在首次開機、使用者的 `runcmd` 執行之後修改 SSH：

- 替換 `/etc/ssh/sshd_config` 與 `/etc/ssh/sshd_config.d/*.conf` 中生效的 `Port` 設定，檢查 SSH 設定後重啟 SSH。
- 若 SSH 由 `ssh.socket` 啟動（例如 Ubuntu），另外寫入 `/etc/systemd/system/ssh.socket.d/zz-kvm-vm-port.conf`。之後若手動改連接埠，請一併修改或刪除此檔。
- 若 SSH 無法在新連接埠啟動（例如被 SELinux 或其他服務擋住），kvm-vm 會還原原本的 SSH 檔案並重啟 SSH。guest 仍可從原本的連接埠連線（新 VM 為 22），cloud-init 會回報錯誤，`/var/log/cloud-init-output.log` 中會出現 `kvm-vm: could not move SSH to port <port>`。

請讓 guest 內與上游的防火牆放行該連接埠。Rocky Linux、AlmaLinux 等啟用 SELinux 的 guest，若 policy 尚未把該連接埠指派給 SSH，需先授權。將以下內容合併到 `cloud_init` 區段：

```yaml
cloud_init:
  ssh_port: 2222
  packages:
    - policycoreutils-python-utils
  runcmd:
    - [semanage, port, -a, -t, ssh_port_t, -p, tcp, '2222']
```

其他指令如何處理連接埠：

- `clone` 使用目標定義檔的連接埠，未設定時為 22。使用 22 時，會移除來源 guest 的 `Port` 設定與 socket 設定檔，讓 SSH 回到映像檔預設值。
- `reinstall` 沿用已儲存的連接埠。
- 只修改定義檔不會改變執行中的 VM。

## 環境變數覆寫

環境變數可在執行時覆寫部分定義值。對 `validate`、`create`、`clone` 而言，環境變數優先於 YAML；`reinstall` 一律使用已儲存的定義檔，並忽略這些變數。

```bash
sudo KVM_VM_OS=debian13 KVM_VM_NAME=web02 KVM_VM_VCPUS=16 KVM_VM_DISK=100 \
  KVM_VM_MEMORY=32000 KVM_VM_IPV4=192.168.10.52/24 \
  kvm-vm create examples/ubuntu24-web01.yaml
```

此例以 `ubuntu24-web01.yaml` 為範本，不必另寫檔案就建立了另一台 VM `web02`。

| 變數 | 覆寫 | 範例 | 說明 |
|---|---|---|---|
| `KVM_VM_NAME` | `vm.name` | `web02` | |
| `KVM_VM_VCPUS` | `vm.vcpus` | `4` | 1 到 1024。 |
| `KVM_VM_MEMORY` 或 `KVM_VM_MEMORY_MIB` | `vm.memory_mib` | `4096` | 至少 256。 |
| `KVM_VM_DISK` 或 `KVM_VM_DISK_GIB` | `storage.disk_gib` | `50` | 至少 1。 |
| `KVM_VM_OS` | `image.distro` 與 `vm.os_variant` | `rocky9` | 必須是[內建別名](#內建別名)，`os_variant` 會設為別名本身，再依 [os_variant](#os_variant) 的步驟選擇。`clone` 時只設定 `os_variant`。 |
| `KVM_VM_BRIDGE` | `network.mode`、`network.bridge` | `br0` | 選用橋接模式，不可與 Macvtap 並用。 |
| `KVM_VM_MACVTAP` | `network.mode`、`network.parent` | `eno1` | 選用 Macvtap 模式，即使定義檔是橋接或 NAT 也一樣。空值會報錯。 |
| `KVM_VM_IPV4` | `network.ipv4` | `dhcp` 或 `192.168.1.50/24` | `dhcp`、`disabled`，或含前綴長度的靜態位址。 |
| `KVM_VM_GATEWAY` | `network.ipv4.gateway` | `192.168.1.1` | |
| `KVM_VM_DNS` | `network.ipv4.dns` | `1.1.1.1,8.8.8.8` | 以逗號分隔。 |

Console 密碼登入無法用環境變數開啟。它在 YAML 中設定，憑證檔則以 `--console-password-file` 指定。

## 宿主機路徑

`install.sh` 會建立以下目錄，每個目錄都可用環境變數改到其他位置。

| 變數 | 預設值 | 內容 |
|---|---|---|
| `KVM_VM_DEF_DIR` | `/etc/kvm-vm/definitions` | 生效定義檔 `NAME.yaml`。 |
| `KVM_VM_STATE_DIR` | `/var/lib/kvm-vm/state` | 狀態清單、快照紀錄（`snapshots/NAME/`）與生命週期鎖（`.lifecycle-lock`）。 |
| `KVM_VM_BASE_DIR` | `/var/lib/libvirt/images/base` | 快取的雲端映像檔。 |
| `KVM_VM_DISK_DIR` | `/var/lib/libvirt/images/vm` | VM 系統磁碟 `NAME.qcow2`。 |
| `KVM_VM_CLOUD_DIR` | `/var/lib/libvirt/images/cloud-init` | 各 VM 的 cloud-init seed 與來源檔。 |
| `KVM_VM_LIBVIRT_URI` | `qemu:///system` | libvirt 連線。 |

磁碟、映像檔與 cloud-init 目錄都位於 `/var/lib/libvirt/images` 下，因此適用一般的 libvirt/QEMU 擁有權與 SELinux 標籤。若系統有 `restorecon`，kvm-vm 會對新檔案執行它。

管理同一個 libvirt 的所有 kvm-vm 指令都必須使用相同的值。生命週期鎖位於狀態目錄中，使用不同狀態目錄的兩個行程彼此看不到對方。

## 用 Git 管理定義檔

把自己撰寫的 YAML 檔放進 Git，例如：

```text
infra/kvm/
  web01.yaml
  web02.yaml
  db01.yaml
```

```bash
kvm-vm validate infra/kvm/web01.yaml
sudo kvm-vm create infra/kvm/web01.yaml
sudo kvm-vm status web01
```

請把 `/etc/kvm-vm/definitions/*.yaml` 視為實際部署的紀錄，而不是設定的來源。它記錄了宿主機上實際使用的值，包括自動產生的 MAC 位址。修改它不會改變 VM；`reinstall` 會讀取它，以相同設定重建 VM。
