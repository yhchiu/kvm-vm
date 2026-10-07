[English](README.md) | 繁體中文

# kvm-vm

`kvm-vm` 是一個專為長期運行的獨立 KVM/libvirt 主機設計的輕量宣告式虛擬機器（VM）管理工具。
它使用標準工具（`virsh`、`virt-install`、`qemu-img`、cloud-init），並為所建立的每個虛擬機器儲存一份生效的 YAML 定義（effective YAML definition）以及小型的狀態清單（state manifest）。

其設計刻意保持低於 OpenStack/Proxmox 等系統的複雜度，同時讓可重複執行的虛擬機器佈署比臨時拼湊的 `virt-install` 命令列更加安全可靠。

## 指令

```text
kvm-vm create <vm.yaml> [--refresh-image] [--no-start] [--dry-run]
kvm-vm clone <source-vm> <target.yaml> [--no-start] [--dry-run]
kvm-vm reinstall <vm> [--os <os>] [--image-url <url>] [--image-path <path>] [--refresh-image] [--no-start] [--dry-run] [--yes] [--force]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
kvm-vm validate <vm.yaml> [--clone]
```

## 主機依賴套件

### Python 需求
- **Python 3.8+**
- **執行期相依套件**：`PyYAML`（Debian/Ubuntu 為 `python3-yaml`，RHEL 系列為 `python3-pyyaml`，或 `pip install pyyaml`）。其餘模組全數使用 Python 標準函式庫。
- **無須 C 語言綁定套件**：`kvm-vm` 刻意透過 subprocess 調用系統標準 CLI 工具（`virsh`、`virt-install`、`qemu-img`），**不依賴 `libvirt-python`**，避免因宿主機 libvirt 版本不同產生編譯或 ABI 相容性問題。
- **測試相依套件（選用）**：`pytest`（Debian/Ubuntu 為 `python3-pytest`，或 `pip install pytest`），用於執行單元測試與 E2E 測試。

### 系統套件

Debian / Ubuntu（套件名稱可能因發行版本而略有不同）：

```bash
apt install \
  qemu-system-x86 qemu-utils \
  libvirt-daemon-system libvirt-clients \
  virt-install cloud-image-utils \
  python3 python3-yaml \
  guestfs-tools libosinfo-bin iproute2 openssh-client
```

*(附註：在 Debian 11 或較舊的發行版上，請使用 `virtinst` 代替 `virt-install`，並使用 `libguestfs-tools` 代替 `guestfs-tools`)*

RHEL / Rocky / Alma 系列：

```bash
dnf install \
  qemu-kvm qemu-img libvirt virt-install \
  xorriso python3 python3-pyyaml \
  guestfs-tools libosinfo iproute openssh-clients

# 啟動並設定開機自啟 libvirt 服務（RHEL 系列安裝後預設不會自動啟動服務）
sudo systemctl enable --now libvirtd
```

*(附註：在 RHEL 8 / Rocky 8 等較舊的發行版上，請使用 `genisoimage` 代替 `xorriso`，並使用 `libguestfs-tools` 代替 `guestfs-tools`)*

建立 cloud-init seed 映像檔時，`kvm-vm` 會自動偵測並優先使用宿主機上可用的工具：`cloud-localds`（來自 `cloud-image-utils` 或 `cloud-utils`）、`genisoimage`、`mkisofs` 或 `xorriso`。

`virt-sysprep` 與 `virt-customize` 僅在執行 `clone` 時需要，但仍建議在管理主機上安裝。

## 安裝

```bash
sudo ./install.sh
```

預設的 libvirt URI 為 `qemu:///system`。

### 升級

```bash
sudo ./install.sh
```

安裝腳本具有冪等性（idempotent），重複執行即可原地升級 `kvm-vm`。

## 目錄結構

```text
/etc/kvm-vm/definitions/             生效的 YAML 定義
/var/lib/kvm-vm/state/               工具狀態清單
/var/lib/libvirt/images/base/        快取的發行版雲端映像檔
/var/lib/libvirt/images/vm/          各虛擬機器獨立的 qcow2 系統磁碟
/var/lib/libvirt/images/cloud-init/  各虛擬機器的 NoCloud seed 與來源檔案
```

Cloud-init 媒介檔案存放於 `/var/lib/libvirt/images` 目錄下，以自然符合常見的 libvirt/QEMU 檔案擁有權與 SELinux 策略。在啟用 SELinux 的主機上，若系統支援則會自動調用 `restorecon`。

所有儲存路徑皆可透過環境變數進行覆寫；詳見[環境變數](#環境變數)。

## 建立虛擬機器

可從 `examples/ubuntu24-web01.yaml` 開始，修改網路設定與 SSH 金鑰路徑。預設的網路模式為 `bridge`，預設橋接介面為 `br0`（可透過環境變數 `KVM_VM_BRIDGE` 自訂）。

```bash
kvm-vm validate web01.yaml
sudo kvm-vm create web01.yaml
```

環境變數可與 YAML 檔案搭配使用，在部署時覆寫特定的設定值（詳見[環境變數](#環境變數)）：

```bash
sudo KVM_VM_OS=debian13 KVM_VM_NAME=web02 KVM_VM_VCPUS=16 KVM_VM_DISK=100 \
  KVM_VM_MEMORY=32000 KVM_VM_IPV4=192.168.10.52/24 \
  kvm-vm create examples/ubuntu24-web01.yaml
```

此範例以 `examples/ubuntu24-web01.yaml` 作為基礎定義，透過環境變數覆寫了作業系統、名稱、資源規格與 IP 位址，在不需撰寫新 YAML 檔案的情況下建立一台完全不同的虛擬機器（`web02`）。

`create` 目前支援以下內建的便捷發行版別名（distro aliases）：

```text
ubuntu24.04
ubuntu26.04
debian12
debian13
rocky8
rocky9
rocky10
almalinux8
almalinux9
almalinux10
```

您也可以改為提供自訂的映像檔：

```yaml
storage:
  disk_gib: 80
  image:
    path: /srv/images/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

或：

```yaml
storage:
  disk_gib: 80
  image:
    url: https://images.example.com/company-ubuntu.qcow2
    sha256: 0123456789abcdef...
```

若要在正式環境中達到可重複驗證的佈署，使用固定的 URL/本機映像檔路徑搭配 `sha256` 會比隨時間變動的 `current`/`latest` 發行版別名更加理想。

每個虛擬機器都會取得一個**完整且獨立的 qcow2 磁碟**。已存在的虛擬機器不會依賴快取的基底映像檔，因此重新整理（refresh）或刪除基底映像檔都不會影響到它們。

### 連線至虛擬機器

虛擬機器建立並完成 cloud-init 首次初始化後：

1. **查詢獲配的 IP 位址**：

```bash
kvm-vm list --ips
# 或檢視詳細狀態：
kvm-vm status web01
```

2. **透過 SSH 連線**：

使用定義檔中 `cloud_init.user` 所設定的使用者名稱（範例預設為 `admin`）及對應的 SSH 私鑰連線：

```bash
ssh admin@<vm-ip> -i ~/.ssh/id_ed25519
```

> [!NOTE]
> **僅限 SSH 金鑰登入安全策略**：
> `kvm-vm` 建立的虛擬機器預設禁用 SSH 密碼認證與 root 直接登入（`lock_passwd: true`, `ssh_pwauth: false`, `disable_root: true`）。
> 詳細的 root 密碼設定、主控台登入方式以及緊急 SSH 金鑰救援，請參閱[安全策略與帶外救援維護](#安全策略與帶外救援維護)。

## YAML 定義範例

範例：

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
  ssh_authorized_keys:
    - file:/root/.ssh/id_ed25519.pub
  package_update: false
  qemu_guest_agent: true
  timezone: Asia/Taipei
  packages:
    - curl
    - vim
  runcmd: []
```

SSH 金鑰可以是純文字 OpenSSH 公鑰字串，或是 `file:/path/to/key.pub` 格式。相對的 `file:` 路徑會以該 YAML 檔案所在位置為基準進行解析。

`mac: auto` 在儲存的生效 YAML 中會被替換為實際的 `52:54:00:*` 位址。靜態 cloud-init 網路設定會比對該 MAC 位址並將網路介面重新命名為 `eth0`，因此設定不會受到不同發行版將介面命名為 `ens3`、`enp1s0` 等差異的影響。

## 環境變數

所有儲存路徑、libvirt 連線 URI 以及虛擬機器設定值皆可透過環境變數進行覆寫（優先權高於 YAML 定義與內建預設值）：

### 路徑與系統配置

| 變數 | 預設值 | 說明 |
|---|---|---|
| `KVM_VM_LIBVIRT_URI` | `qemu:///system` | libvirt 連線 URI |
| `KVM_VM_BASE_DIR` | `/var/lib/libvirt/images/base` | 快取的發行版雲端映像檔目錄 |
| `KVM_VM_DISK_DIR` | `/var/lib/libvirt/images/vm` | 各虛擬機器獨立的 qcow2 系統磁碟目錄 |
| `KVM_VM_STATE_DIR` | `/var/lib/kvm-vm/state` | 工具狀態清單目錄 |
| `KVM_VM_CLOUD_DIR` | `/var/lib/libvirt/images/cloud-init` | 各虛擬機器 NoCloud seed 與來源檔案目錄 |
| `KVM_VM_DEF_DIR` | `/etc/kvm-vm/definitions` | 生效的 YAML 定義儲存目錄 |

### 虛擬機器定義覆寫

| 變數 | YAML 路徑 | 範例 | 說明 |
|---|---|---|---|
| `KVM_VM_NAME` | `vm.name` | `web02` | 覆寫虛擬機器名稱 |
| `KVM_VM_VCPUS` | `vm.vcpus` | `4` | 覆寫 vCPU 核心數（1-1024） |
| `KVM_VM_MEMORY` / `KVM_VM_MEMORY_MIB` | `vm.memory_mib` | `4096` | 覆寫記憶體容量（MiB，>= 256） |
| `KVM_VM_DISK` / `KVM_VM_DISK_GIB` | `storage.disk_gib` | `50` | 覆寫磁碟大小（GiB，>= 1） |
| `KVM_VM_OS` | `image.distro` / `os_variant` | `rocky9` | 覆寫發行版別名與 `os_variant` |
| `KVM_VM_BRIDGE` | `network.bridge` | `br0` | 橋接模式下預設或覆寫使用的 Bridge 名稱 |
| `KVM_VM_IPV4` | `network.ipv4` | `dhcp`, `192.168.1.50/24` | 設定 IP 取得方式（`dhcp`, `disabled`）或靜態 CIDR 位址 |
| `KVM_VM_GATEWAY` | `network.ipv4.gateway` | `192.168.1.1` | 覆寫預設 IPv4 閘道 |
| `KVM_VM_DNS` | `network.ipv4.dns` | `1.1.1.1,8.8.8.8` | 覆寫 DNS 伺服器列表（以逗號分隔） |

## 網路模式

`kvm-vm` 支援兩種單網卡（single-NIC）連接模式。

### 橋接模式（Bridge mode）

橋接模式為預設模式。如果省略 `network.mode` 與 `network.bridge`，虛擬機器將連接至 `br0`（或由 `KVM_VM_DEFAULT_BRIDGE` 指定的橋接介面）：

```yaml
network:
  mode: bridge
  bridge: br0
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
```

這會產生等同於以下的指令參數：

```text
virt-install ... --network bridge=br0,model=virtio,mac=...
```

為了向後相容，僅包含 `network.bridge` 的現有 YAML 仍會採用橋接模式。預設橋接介面為 `br0`，亦可透過環境變數 `KVM_VM_BRIDGE` 在主機層級進行自訂。

### NAT 模式（NAT mode）

NAT 模式將虛擬機器連接至 libvirt 虛擬網路，而非直接連接至該虛擬網路的 Linux 橋接介面。常見的 libvirt `default` 網路通常底層由 `virbr0`、dnsmasq/DHCP 以及對外 NAT 所支援。

```yaml
network:
  mode: nat
  libvirt_network: default
  model: virtio
  mac: auto
  ipv4:
    method: dhcp
  ipv6:
    method: disabled
```

這會產生等同於以下的指令參數：

```text
virt-install ... --network network=default,model=virtio,mac=...
```

在執行 `create` 或 `clone` 之前，`kvm-vm` 會驗證所指定的 libvirt 網路是否存在、是否為啟動狀態（active），以及是否已設定 NAT 轉發。若網路存在但尚未啟動，可透過以下指令啟動：

```bash
virsh -c qemu:///system net-start default
virsh -c qemu:///system net-autostart default
```

`examples/ubuntu24-nat.yaml` 提供了完整的 NAT 範例。對於 NAT 網路而言，DHCP 通常是最簡單的選擇，因為 IP 位址分配會由 libvirt 網路的 DHCP 服務負責管理。雖然系統也允許設定客體（Guest）靜態 IP，但您必須自行確保該位址位於 NAT 子網路內，且不會與 DHCP 分配或保留範圍發生衝突。

## 列出與檢視狀態

快速列出虛擬機器（不偵測 IP）：

```bash
kvm-vm list
```

僅列出由本工具管理的虛擬機器：

```bash
kvm-vm list --managed
```

包含 IP 偵測（較慢，因為會探查 guest-agent、DHCP 租約以及 ARP 資料）：

```bash
kvm-vm list --ips
```

詳細檢視：

```bash
kvm-vm status web01
```

`status` 會顯示 libvirt 網域資訊、kvm-vm 狀態清單、磁碟、網路介面以及 libvirt 已知的 IP 位址。

## 序列主控台（Serial console）

```bash
kvm-vm console web01
```

使用 `Ctrl+]` 退出 `virsh console`。

> **附註**：如[安全策略](#安全策略與帶外救援維護)所述，使用者與 root 密碼預設為鎖定狀態（`lock_passwd: true`）。若需從主控台鍵盤登入，請參閱下方的 [Root 密碼設定](#root-密碼設定root-password-configuration)。

## 安全策略與帶外救援維護

`kvm-vm` 預設透過 cloud-init 套用嚴格的生產級安全策略：
- **僅限 SSH 金鑰**：`ssh_pwauth: false` 禁用 SSH 密碼認證。
- **使用者密碼鎖定**：`lock_passwd: true` 鎖定預設使用者的密碼。
- **禁用 root 登入**：`disable_root: true` 防止直接以 root 身分登入。

由於密碼預設鎖定，剛建立的虛擬機器**無法直接透過序列主控台（`kvm-vm console <vm>`）進行密碼登入**。當遇到無網路環境需本機登入或私鑰遺失時，請透過以下帶外（out-of-band）方式處理：

### Root 密碼設定（Root password configuration）

若需要設定 root 密碼以供本機序列主控台登入：

1. **推薦方式（停機透過 `virt-customize` 帶外注入）**：
   將虛擬機器關機後，透過標準輸入（stdin）安全寫入 root 密碼。強烈推薦此方式，因為它能**避免明文密碼外洩於 YAML 定義檔、Shell 歷史紀錄（`~/.bash_history`）或系統行程列表（`ps`）**：

   ```bash
   # 1. 先將虛擬機器正常關機
   virsh shutdown web01

   # 2. 透過 stdin 安全設定 root 密碼
   virt-customize -d web01 --root-password file:/dev/stdin
   # （輸入密碼後按 Enter，再按 Ctrl+D 結束）
   ```

   亦可直接指定虛擬磁碟路徑或從具備權限保護的臨時檔案讀取：

   ```bash
   virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 --root-password file:/path/to/file
   ```

2. **建立時設定（透過 `runcmd`）**：
   在建立 VM 前，亦可直接於 YAML 定義檔中透過 cloud-init 設定（注意：密碼將以明文留存於 YAML 檔案中）：

   ```yaml
   cloud_init:
     user: admin
     ssh_authorized_keys:
       - file:~/.ssh/id_ed25519.pub
     runcmd:
       - "echo 'root:YourPasswordHere' | chpasswd"
   ```

### SSH 金鑰遺失救援（SSH key recovery）

若您遺失了 SSH 私鑰，或因設定錯誤被鎖在系統外且無法登入主控台：

1. 將虛擬機器關機：
   ```bash
   virsh shutdown web01
   ```

2. 使用 `virt-customize` 直接將新的公鑰注入至虛擬磁碟中的使用者帳號：
   ```bash
   virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
   ```
   *（或直接指定磁碟路徑：`virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 --ssh-inject admin:file:~/.ssh/id_ed25519.pub`）*

3. 重新啟動虛擬機器並以新金鑰連線：
   ```bash
   virsh start web01
   ssh admin@<vm-ip> -i /path/to/new_key
   ```

## 安全刪除機制

```bash
kvm-vm delete web01
```

此指令會要求輸入虛擬機器名稱以供確認。若要用於自動化流程：

```bash
kvm-vm delete web01 --yes
```

執行中的虛擬機器**不會**被隱式強制銷毀。請先將其關機，或明確指定：

```bash
kvm-vm delete web01 --force --yes
```

重要安全規則：`kvm-vm` **不會**使用 `virsh undefine --remove-all-storage`。
它僅會刪除記錄於自身狀態清單中的系統磁碟與 cloud-init 媒介檔案。
因此，管理員後續手動掛載的資料磁碟不會被意外刪除。

對於非本工具管理（既有）的虛擬機器，系統會拒絕刪除儲存空間。您可以在保留所有磁碟不變的情況下取消定義（undefine）該虛擬機器：

```bash
kvm-vm delete legacy01 --keep-storage
```

對於由本工具管理的虛擬機器，您也可以選擇保留所有受管理的檔案：

```bash
kvm-vm delete web01 --keep-storage
```

在這種情況下，狀態與定義檔仍會保留，以避免儲存空間默默成為孤立檔案（orphan storage）。

## 重灌虛擬機器（Reinstall）

重灌現有的受控虛擬機器，會從雲端基礎映像檔（base cloud image）重新佈署一份全新的乾淨作業系統磁碟，**同時保留該虛擬機器原本的所有其他配置**（vCPU 核心數、記憶體、磁碟容量大小、已分配的 MAC 位址、靜態/DHCP IP、cloud-init 使用者與 SSH 金鑰、網路模式與 Bridge 等）：

```bash
# 使用現有定義中的作業系統進行重灌
kvm-vm reinstall web01

# 重灌並更換為不同的作業系統
kvm-vm reinstall web01 --os debian13
```

### 選項參數

- `--os <distro|url|path>`：發行版別名（`ubuntu24.04`、`ubuntu26.04`、`debian12`、`debian13`、`rocky8`、`rocky9`、`rocky10`、`almalinux8`、`almalinux9`、`almalinux10`）、自訂映像檔 URL 或本機映像檔路徑。
- `--image-url <url>`：明確指定雲端映像檔下載 URL。
- `--image-path <path>`：明確指定本機雲端映像檔路徑。
- `--os-variant <variant>`：指定供 `virt-install` 最佳化使用的 libosinfo OS variant 名稱。
- `--refresh-image`：強制重新下載雲端基礎映像檔（即使快取已存在）。
- `--no-start`：重灌並在 libvirt 中完成定義，但不立即開機。
- `--force`：若虛擬機器目前正在運行中，強制將其關閉並進行重灌（若未指定 `--force`，運行中的虛擬機器將拒絕重灌）。
- `--yes`：略過互動式確認提示（在自動化腳本或非互動環境中必備）。
- `--dry-run`：執行所有事前檢查並輸出生效的 YAML 定義，不會異動任何磁碟或 libvirt 網域。

```bash
# 自動化重灌範例（更換 OS 並強制關機執行）
kvm-vm reinstall web01 --os rocky9 --force --yes
```

## 複製虛擬機器（Clone）

複製目標的 YAML 格式請參閱 `examples/clone-target.yaml`。它刻意不需要 `storage.image` 欄位，因為來源虛擬機器的磁碟本身就是來源映像檔。

```bash
virsh shutdown web01
# 等待狀態變更：virsh domstate web01 -> "shut off"
kvm-vm validate --clone web02.yaml
sudo kvm-vm clone web01 web02.yaml
```

複製功能刻意**僅支援離線操作（offline only）**。來源網域必須處於關機（shut off）狀態。

處理流程如下：

```text
來源系統磁碟
      |
      +-- qemu-img convert --> 獨立的目標 qcow2
                                  |
                                  +-- virt-sysprep
                                  |     machine-id
                                  |     SSH 主機金鑰
                                  |     hostname/網路殘留狀態
                                  |     DHCP 狀態
                                  |     隨機種子（random seed）
                                  |
                                  +-- cloud-init clean
                                  |
                                  +-- 新 MAC + 新 NoCloud instance-id
                                  |
                                  +-- 目標 YAML 的網路/使用者設定
```

此處僅執行經過明確挑選的安全 `virt-sysprep` 操作。腳本**不會**執行範圍廣泛的預設操作集，因為複製不應該無預警地清除使用者帳號或不相關的應用程式狀態。

目前的複製限制均為刻意設計：

- 僅支援 Linux 客體系統（`virt-sysprep` 的限制）。
- 來源虛擬機器必須處於關機狀態。
- 系統磁碟目前必須是一般檔案形式（file-backed）的磁碟。
- 僅會複製系統磁碟；額外掛載的資料磁碟不會被複製。
- 拒絕將目標虛擬磁碟縮小。允許設定較大的容量；擴充磁碟分割區與檔案系統仍由雲端映像檔或客體系統工具負責處理。

### 驗證 virt-sysprep 相容性

在進行複製之前，若要測試主機上的 `virt-sysprep` 是否支援目標客體作業系統：

1. **檢查主機支援的操作**：
   ```bash
   virt-sysprep --list-operations
   ```
   `kvm-vm` 至少需要 `machine-id` 與 `ssh-hostkeys`。

2. **測試作業系統探測（OS inspection）**：
   `virt-sysprep` 仰賴 `libguestfs` 的探測功能。驗證客體作業系統結構是否能被正確辨識：
   ```bash
   virt-inspector -a /path/to/image.qcow2
   ```
   探測成功會輸出包含 `<operatingsystem>`、`<distro>`、`<version>` 與掛載點的 XML。

3. **使用 `--dry-run` 模擬操作**：
   使用 `kvm-vm` 所執行的完全相同操作集，對映像檔進行非破壞性的模擬測試：
   ```bash
   virt-sysprep --dry-run -a /path/to/image.qcow2 \
     --operations machine-id,ssh-hostkeys,ssh-userdir,net-hostname,net-hwaddr,dhcp-client-state,random-seed
   ```
   若執行成功且無掛載錯誤或未辨識作業系統的警告，即表示 `virt-sysprep` 完全相容。

## CPU 模型注意事項

範例中設定：

```yaml
cpu: host-passthrough
```

這能最大程度使用主機 CPU 功能，適用於穩定的單一主機或同質硬體主機環境。若未來有在不同世代 CPU 之間進行即時遷移（live migration）的需求，請改為明確指定支援遷移的 CPU 模型。

## 重新整理基底映像檔

在先前的 `create` 流程中所下載的發行版雲端映像檔會被快取於本地（預設路徑為 `/var/lib/libvirt/images/base/`）。後續建立 VM 時若快取已存在，預設會直接重用該映像檔，以避免重複下載。

若上游發行版已有更新，且您希望拉取最新版本的映像檔來建立新 VM，可加上 `--refresh-image` 參數：

```bash
kvm-vm create newvm.yaml --refresh-image
```

- **不影響既有 VM**：此參數只會更新本地快取的基底映像檔。由於每個 VM 在建立時都會分配獨立的 qcow2 系統磁碟，因此重新下載或更新基底映像檔完全不會影響已建立運行的虛擬機器。
- **無條件重新下載**：經程式碼確認，`--refresh-image` 執行時不會比對 HTTP `ETag` 或 `Last-Modified` 標頭；只要帶入此參數，系統就會無條件重新下載完整映像檔並覆蓋本地快取。因此即使本地快取的映像檔已經是最新版，依然會重新下載。

## `--no-start`

`create`、`clone` 與 `reinstall` 皆支援：

```bash
kvm-vm create vm.yaml --no-start
kvm-vm reinstall web01 --no-start
```

虛擬機器網域會被定義（define）但維持關機狀態。若您想在首次開機前檢查 `virsh dumpxml` 或套用其他額外的 libvirt 策略，此選項非常實用。

## `--dry-run`

`create`、`clone` 與 `reinstall` 皆支援：

```bash
kvm-vm create vm.yaml --dry-run
kvm-vm clone source-vm target.yaml --dry-run
kvm-vm reinstall web01 --os debian13 --dry-run
```

執行所有事前檢查（YAML 正規化、網域/狀態衝突檢查、橋接/NAT 驗證、MAC 位址分配以及 SSH 金鑰解析），並輸出正規化後的 YAML，過程中不會下載映像檔、不會異動磁碟，也不會定義 libvirt 網域。對於 `clone`，它還會在進行任何磁碟轉換前，使用 dry-run 檢查驗證來源磁碟與 `virt-sysprep` 的相容性。

## 建議的維運工作流程

將您撰寫的 YAML 檔案納入 Git 版控，例如：

```text
infra/
  kvm/
    web01.yaml
    web02.yaml
    db01.yaml
```

接著執行：

```bash
kvm-vm validate infra/kvm/web01.yaml
kvm-vm create infra/kvm/web01.yaml
kvm-vm status web01
```

請將 `/etc/kvm-vm/definitions/*.yaml` 視為工具產生的**生效佈署記錄（effective deployment record）**，而非主要的事實來源（source-of-truth）。這樣可以將經過審查的預期設定定義，與主機上實際使用的精確設定（包含自動產生的 MAC 位址）清楚分離。

## 使用較新或自訂的 Linux 發行版

`kvm-vm` 不受限於內建的發行版別名。支援較新的發行版本（例如 Ubuntu 26.04、Rocky Linux 10、AlmaLinux 10）或自訂映像檔有以下兩種方式：

### 1. 直接指定 URL 或本機檔案路徑（無需修改程式碼）

任何支援 cloud-init 的 qcow2/raw 映像檔都可以透過指定 `url` 或 `path` 直接使用：

```yaml
vm:
  name: web01
  # 選用：指定 os_variant 以利 virt-install 最佳化。
  # 若主機的 libosinfo 尚未收錄該全新版本，可指定前一個版本或直接省略。
  os_variant: ubuntu24.04

storage:
  disk_gib: 40
  image:
    url: https://cloud-images.ubuntu.com/resolute/current/resolute-server-cloudimg-amd64.img
    # 或本機路徑：
    # path: /var/lib/libvirt/images/base/Rocky-10-GenericCloud-Base.latest.x86_64.qcow2
```

### 2. 在 `kvm-vm` 中新增發行版別名

若想為其他發行版使用簡短別名（例如 `distro: fedora41`），可在 `kvm-vm` 腳本中的 `DISTROS` 字典加入項目：

```python
DISTROS = {
    ...
    "fedora41": {
        "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/41/Cloud/x86_64/images/Fedora-Cloud-Base-Generic.x86_64-41-1.4.qcow2",
        "cache": "Fedora-Cloud-Base-Generic.x86_64-41-1.4.qcow2",
        "os_variant": "fedora41",
    },
}
```

較新發行版的重要注意事項：
- **`os_variant` 與 `libosinfo` 相容性**：
  - **自動降級至 `generic`**：`kvm-vm` 在建立 VM 前會自動透過 `osinfo-query os` 檢查。若指定的 `os_variant` 未被主機的 `libosinfo` 收錄，系統會記錄警告訊息並自動安全降級為 `--os-variant generic`，而不會造成建立失敗或中斷。
  - **對伺服器 VM 幾乎無影響**：由於 `kvm-vm` 採用無圖形介面（`--graphics none`），且已明確指定使用高效能的 `virtio` 磁碟匯流排與 `virtio` 網卡模型，降級為 `generic` 不會影響開機，對標準伺服器效能亦無負面影響。
  - **最佳實踐建議**：若希望 `virt-install` 能套用貼近目標系統的虛擬硬體拓撲最佳化，但在主機 `libosinfo` 更新前，建議在 YAML 的 `vm.os_variant` 明確指定前一個相容的主要版本（例如 Rocky Linux 10 指定為 `rocky9`，或 Ubuntu 26.04 指定為 `ubuntu24.04`）。
- **複製時的 Sysprep 支援**：若使用 `kvm-vm clone`，請確保主機的 `libguestfs-tools` / `virt-sysprep` 版本支援該客體作業系統結構（machine-id、網路設定等）。測試方式請參閱[驗證 virt-sysprep 相容性](#驗證-virt-sysprep-相容性)。

## 本工具刻意不處理的事項

本工具並非叢集管理員（Cluster Manager）的替代品。目前**不**處理以下功能：

- 即時遷移（live migration）
- Ceph/RBD 儲存系統
- 快照 / 備份（snapshots/backups）
- 多張網路卡（multiple NICs）
- 多個受管理的資料磁碟
- VLAN 標籤（VLAN tagging）
- Windows sysprep
- 已建立虛擬機器的狀態調和 / 偏離校正（reconciliation / drift correction）

上述功能未來可在不更動基本 YAML / 狀態模型的前提下擴充加入。

## 測試

### 前置需求

```bash
pip install pytest
```

### 單元測試

單元測試涵蓋純 Python 函式（YAML 正規化、MAC 位址產生、SSH 金鑰解析、下載重試邏輯、CLI 參數解析），不需要 libvirt 環境或 root 權限。

```bash
python3 -m pytest tests/test_unit.py -v
```

### 冒煙測試（Smoke tests）

冒煙測試腳本會編譯檢查主腳本語法、執行 `--help` / `--version`、在有安裝 pytest 時執行單元測試，並使用正規化器驗證範例 YAML 檔案。此測試不需要運行中的 libvirt 守護行程（daemon）。

```bash
bash tests/smoke.sh
```

### 端到端測試（E2E tests）

實機端到端測試會在真實的 Linux KVM/libvirt 主機上執行完整的 VM 生命週期驗證：
- 下載雲端基礎映像檔（預設：Debian 12）
- 產生 cloud-init seed ISO 並透過 NAT 虛擬網路建立虛擬機
- 等待 DHCP 租約 IP 並驗證 SSH 連線、主機名設定與使用者建立
- 驗證 `kvm-vm status` 與 `kvm-vm list` 輸出結果
- 停機進行 `kvm-vm clone` 複製並由 `virt-sysprep` 重置系統後開機連線
- 測試 `kvm-vm reinstall --force --yes` 線上重裝作業系統
- 完整刪除與儲存檔案清理驗證

**前置需求：**
- 具備硬體虛擬化（`/dev/kvm`）的 Linux 主機
- `root` 執行權限（需使用 `sudo`）
- 運作中的 libvirt 守護行程以及已啟動的 `default` NAT 虛擬網路
- 必要 CLI 工具（`virsh`、`virt-install`、`qemu-img`、`virt-sysprep`、`cloud-localds`/`genisoimage`/`xorriso`、`ssh`）

執行前置環境檢查與 E2E 測試：

```bash
sudo bash tests/run_e2e.sh
```

或指定特定發行版與詳細輸出：

```bash
sudo bash tests/run_e2e.sh --distro ubuntu24.04 -v
```

