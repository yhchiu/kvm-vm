[English](README.md) | 繁體中文

# kvm-vm

`kvm-vm` 以簡短的 YAML 檔，在單一 KVM/libvirt 宿主機上建立與管理 VM。它使用標準工具（`virt-install`、`virsh`、`qemu-img`、cloud-init），並為每台建立的 VM 保存兩份紀錄：生效定義檔與狀態清單。之後的 `reinstall`、`clone`、`delete` 等指令都依這些紀錄運作，不必猜測 VM 當初是怎麼建立的。

它取代的是手寫的 `virt-install` 指令，而不是 OpenStack 或 Proxmox。詳見[kvm-vm 不做的事](#kvm-vm-不做的事)。

- [快速上手](#快速上手)
- [常用指令](#常用指令)
- [文件導覽](#文件導覽)
- [安全規則](#安全規則)
- [系統需求](#系統需求)
- [kvm-vm 不做的事](#kvm-vm-不做的事)

## 快速上手

以下步驟會在 libvirt 的 `default` NAT 網路上建立第一台 VM，不需更動宿主機的網路設定。請在 KVM 宿主機上執行。

**1. 安裝宿主機套件。**

Debian / Ubuntu：

```bash
sudo apt install qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-clients \
  virt-install cloud-image-utils python3 python3-yaml \
  guestfs-tools libosinfo-bin iproute2 openssh-client openssl
```

RHEL / Rocky Linux / AlmaLinux 9 以上：

```bash
sudo dnf install qemu-kvm qemu-img libvirt virt-install xorriso python3 python3-pyyaml \
  guestfs-tools libosinfo iproute openssh-clients openssl
sudo systemctl enable --now libvirtd
```

較舊的發行版請見[系統需求](#系統需求)。

**2. 安裝 kvm-vm**（在本專案的 checkout 目錄中）：

```bash
sudo ./install.sh
```

會安裝 `/usr/local/sbin/kvm-vm` 並建立所需目錄。重新執行即可升級。

**3. 確認已有 SSH 金鑰。** 範例使用 `~/.ssh/id_ed25519.pub`，若不存在，請用 `ssh-keygen -t ed25519` 建立。

**4. 確認 NAT 網路已啟動。**

```bash
sudo virsh net-list --all
```

若 `default` 不是 `active`，請啟動並設定自動啟動：

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

**5. 建立 VM。** [`examples/ubuntu24-nat.yaml`](examples/ubuntu24-nat.yaml) 描述一台名為 `nat01` 的 Ubuntu 24.04 VM，有 2 個 vCPU、4 GiB 記憶體與 40 GiB 磁碟。

```bash
sudo kvm-vm validate examples/ubuntu24-nat.yaml          # 檢查檔案並列出所有值
sudo kvm-vm create examples/ubuntu24-nat.yaml --dry-run  # 一併檢查宿主機
sudo kvm-vm create examples/ubuntu24-nat.yaml
```

第一次執行會下載 Ubuntu 雲端映像檔，可能需要幾分鐘。

**6. 連線。** VM 開機後，cloud-init 需要一兩分鐘完成設定。

```bash
sudo kvm-vm list --ips    # 查詢位址
ssh admin@<vm-ip>         # admin 即 cloud_init.user
```

要移除這台測試 VM：`sudo kvm-vm delete nat01 --force --yes`。

**下一步**：參考 [VM 定義檔參考](docs/configuration.zh-TW.md)撰寫自己的定義檔，並用橋接或 Macvtap 模式讓 VM 接上 LAN（[網路設定](docs/networking.zh-TW.md)）。

## 常用指令

請以 root 執行，例如透過 `sudo`。`kvm-vm 指令 --help` 會列出所有選項，[指令參考](docs/commands.zh-TW.md)有詳細說明。

| 想做的事 | 指令 |
|---|---|
| 檢查定義檔（離線） | `kvm-vm validate vm.yaml` |
| 在此宿主機預演建立 | `kvm-vm create vm.yaml --dry-run` |
| 建立 VM | `kvm-vm create vm.yaml` |
| 列出 VM 與位址 | `kvm-vm list --ips` |
| 查看單台 VM 詳細資訊 | `kvm-vm status web01` |
| 開啟序列主控台（按 `Ctrl+]` 離開） | `kvm-vm console web01` |
| 重灌作業系統並保留設定 | `kvm-vm reinstall web01 [--os debian13]` |
| 複製一台已關機的 VM | `kvm-vm clone web01 web02.yaml` |
| 刪除 VM 與 kvm-vm 建立的檔案 | `kvm-vm delete web01` |
| 從 libvirt 移除 VM 但保留檔案 | `kvm-vm delete web01 --keep-storage` |
| 變更前建立回復點 | `kvm-vm snapshot create web01` |
| 保留變更 | `kvm-vm snapshot delete web01 --yes` |
| 撤銷變更 | `kvm-vm snapshot rollback web01 --yes --force` |
| 還原前檢查備份 | `kvm-vm backup check /backups/web01` |

常用選項：`--dry-run` 只檢查不變更，`--yes` 略過確認提示（用於腳本），`--force` 先強制關閉執行中的 VM，`--no-start` 讓 VM 保持關機。

## 文件導覽

| 我想要… | 請閱讀 |
|---|---|
| 撰寫 VM 定義檔：欄位、預設值、映像檔、覆寫 | [VM 定義檔參考](docs/configuration.zh-TW.md) |
| 在橋接、NAT、Macvtap 之間做選擇 | [網路設定](docs/networking.zh-TW.md) |
| 查詢指令的作用 | [指令參考](docs/commands.zh-TW.md) |
| 從主控台登入，或重新取得存取權 | [存取與安全](docs/access.zh-TW.md) |
| 安全地使用快照 | [Snapshots](docs/snapshots.md)（英文） |
| 備份 VM 並在另一台宿主機還原 | [Backup and Restore](docs/full-backup-restore.md)（英文） |
| 了解 clone、reinstall、delete 為何拒絕執行 | [Lifecycle operations around recovery state](docs/lifecycle-recovery-safety.md)（英文） |
| 執行測試 | [測試](tests/README.zh-TW.md) |
| 查詢 Snapshot、Backup 等術語 | [Glossary](GLOSSARY.md)（英文） |
| 了解設計背後的理由 | [架構決策紀錄](docs/adr/)（英文） |

## 安全規則

- **`delete` 只移除 kvm-vm 建立的東西**：系統磁碟、cloud-init 檔案、定義檔與狀態。你掛載的資料磁碟永遠不會被刪除。
- **`reinstall` 會清除系統磁碟**，但保留設定與資料磁碟。
- **`clone` 只能用於已關機的 VM**，且只複製系統磁碟。
- **破壞性指令會先確認。** `delete` 與 `reinstall` 會要求輸入 VM 名稱，除非加上 `--yes`；遇到執行中的 VM 會拒絕，除非加上 `--force`。
- **快照不是備份。** 它是同一個儲存空間上、一個短期的回復點。若要能承受宿主機損毀，請使用[備份流程](docs/full-backup-restore.md)（英文）。
- **密碼預設鎖定，SSH 僅接受金鑰。** Console 密碼登入需明確開啟。
- **使用 Macvtap 時，宿主機無法透過網路連到自己的 VM。** 請從其他機器連線，或使用主控台。

## 系統需求

- 具備 KVM（`/dev/kvm`）與 libvirt 的 Linux 宿主機，透過 `qemu:///system` 使用。kvm-vm 已在 Debian 13 與 AlmaLinux 9.8 宿主機上測試。
- Python 3.8 以上與 PyYAML。不需要 libvirt 的 Python 綁定：kvm-vm 直接執行 `virsh`、`virt-install`、`qemu-img`。
- 建立 cloud-init seed 映像檔的工具：`cloud-localds`、`genisoimage`、`mkisofs`、`xorrisofs` 或 `xorriso`，kvm-vm 使用第一個找到的。
- `clone` 需要：`virt-sysprep` 與 `virt-customize`（套件 `guestfs-tools`）。
- Macvtap 需要：`ip`（套件 `iproute2` 或 `iproute`）。
- Console 密碼登入需要：`openssl`。
- 建議安裝：`osinfo-query`（套件 `libosinfo-bin` 或 `libosinfo`）。沒有它時，所有 VM 都使用 `generic` OS variant。

較舊的發行版：

- Debian 11 以前：以 `virtinst` 取代 `virt-install`，以 `libguestfs-tools` 取代 `guestfs-tools`。
- RHEL 8 系列：預設的 `python3` 是 3.6，對 kvm-vm 來說太舊，且這類宿主機未經測試。套件名稱也不同：以 `genisoimage` 取代 `xorriso`，以 `libguestfs-tools` 取代 `guestfs-tools`。

## kvm-vm 不做的事

kvm-vm 管理的是單一宿主機上的 VM。它不會：

- 即時遷移 VM，或管理叢集、Ceph/RBD 等共享儲存；
- 為每台 VM 保留超過一個快照，或自行製作備份（它只在還原前檢查備份）；
- 加入第二張網卡、設定 VLAN tag，或管理資料磁碟（你可以自行掛載資料磁碟，kvm-vm 不會動它們）；
- 準備 Windows guest；
- 讓既有 VM 跟著修改後的定義檔變更（不做 drift correction）。

## 開發

```bash
python3 -m pytest tests -q   # 離線測試；實機測試除非以 root 在 KVM 宿主機上執行，否則會略過
bash tests/smoke.sh
```

實機測試請見[測試](tests/README.zh-TW.md)。

## 授權

GPL-3.0，詳見 [LICENSE](LICENSE)。
