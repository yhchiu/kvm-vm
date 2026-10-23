[English](commands.md) | 繁體中文

# 指令參考

```text
kvm-vm validate <vm.yaml> [--clone]
kvm-vm create <vm.yaml> [--console-password-file <path>] [--refresh-image] [--no-start] [--dry-run]
kvm-vm list [--managed] [--ips]
kvm-vm status <vm>
kvm-vm console <vm>
kvm-vm reinstall <vm> [--os <alias|url|path> | --image-url <url> | --image-path <path>] [--os-variant <variant>]
                      [--console-password-file <path>] [--refresh-image] [--no-start] [--dry-run] [--yes] [--force]
kvm-vm clone <source-vm> <target.yaml> [--console-password-file <path>] [--no-start] [--dry-run]
kvm-vm delete <vm> [--yes] [--force] [--keep-storage]
kvm-vm snapshot create <vm> [--no-quiesce] [--dry-run]
kvm-vm snapshot status <vm>
kvm-vm snapshot delete <vm> [--yes]
kvm-vm snapshot rollback <vm> [--yes] [--force] [--no-start]
kvm-vm backup check <backup-dir> [--until <checkpoint>]
```

`kvm-vm --help` 與 `kvm-vm 指令 --help` 會列出相同的選項。

- [開始之前](#開始之前)
- [validate](#validate) · [create](#create) · [list](#list) · [status](#status) · [console](#console)
- [reinstall](#reinstall) · [clone](#clone) · [delete](#delete)
- [snapshot](#snapshot) · [backup check](#backup-check)
- [指令拒絕執行時](#指令拒絕執行時)

## 開始之前

請以 root 執行 kvm-vm，例如透過 `sudo`。它管理系統層級的 libvirt（`qemu:///system`）與 `/var/lib` 下的檔案。只有 `validate` 不需要 root。

多個指令共用的選項：

| 選項 | 適用指令 | 作用 |
|---|---|---|
| `--dry-run` | `create`、`clone`、`reinstall`、`snapshot create` | 執行檢查後停止，不做任何變更：不下載、不改磁碟、不定義 VM。`create`、`clone`、`reinstall` 另會輸出生效定義檔。 |
| `--no-start` | `create`、`clone`、`reinstall`、`snapshot rollback` | 完成後讓 VM 保持關機，例如想先檢查 `virsh dumpxml`。 |
| `--yes` | `reinstall`、`delete`、`snapshot delete`、`snapshot rollback` | 略過要求輸入 VM 名稱的確認提示。沒有終端機時（例如腳本中）必須使用。 |
| `--force` | `reinstall`、`delete`、`snapshot rollback` | 先強制關閉執行中的 VM（`virsh destroy`）。未指定時，執行中的 VM 會被拒絕。 |
| `--console-password-file` | `create`、`clone`、`reinstall` | 新 Console 密碼的寫入位置。定義檔設定 `console_password: true` 時必填，否則指定會報錯。詳見[存取與安全](access.zh-TW.md#console-密碼登入)。 |

`create`、`clone`、`reinstall`、`delete` 以及會變更狀態的 `snapshot` 指令會取得鎖 `/var/lib/kvm-vm/state/.lifecycle-lock`，同一時間只會有一個在執行。遇到鎖的指令會立即停止。若先前的執行被中斷，鎖會留著；移除前請先閱讀 [Sequencing and interrupted operations](lifecycle-recovery-safety.md#sequencing-and-interrupted-operations)（英文）。

## validate

```bash
kvm-vm validate web01.yaml
kvm-vm validate --clone web02.yaml     # clone 目標，不需映像檔
```

讀取定義檔，套用預設值與[環境變數覆寫](configuration.zh-TW.md#環境變數覆寫)，讀取 SSH 金鑰檔，然後輸出生效定義檔。它不連線 libvirt，因此抓不到宿主機的問題（例如 bridge 不存在）；這類問題請用 `create --dry-run`。

## create

```bash
sudo kvm-vm create web01.yaml
sudo kvm-vm create web01.yaml --dry-run
```

1. 檢查定義檔、名稱是否可用、網路連接與 SSH 金鑰。`mac: auto` 時挑選 MAC 位址。
2. 若映像檔尚未快取則下載（`--refresh-image` 會重新下載，詳見[映像檔快取](configuration.zh-TW.md#映像檔快取與---refresh-image)）。
3. 把映像檔複製成新磁碟 `/var/lib/libvirt/images/vm/NAME.qcow2`，並擴充到 `disk_gib`。
4. 寫入 cloud-init seed，並以 `virt-install` 定義 VM。
5. 儲存生效定義檔與狀態清單。

VM 開機後指令就會結束。之後 cloud-init 會設定 guest，需要一分鐘以上。可在 guest 上等待它完成：

```bash
ssh admin@<vm-ip> sudo cloud-init status --wait
```

若 `create` 在定義 VM 之前失敗，只會刪除本次建立的檔案。之後的失敗會保留所有檔案供檢查，重試時也不會覆寫殘留檔案。若 libvirt 中已沒有該 VM，只剩 kvm-vm 的狀態與定義檔，`create` 可以重新使用該名稱。詳見 [Lifecycle operations around recovery state](lifecycle-recovery-safety.md#sequencing-and-interrupted-operations)（英文）。

## list

```bash
sudo kvm-vm list              # 所有 libvirt VM，速度快
sudo kvm-vm list --managed    # 只列出 kvm-vm 建立的 VM
sudo kvm-vm list --ips        # 一併查詢位址（較慢）
```

欄位：名稱、狀態、是否由 kvm-vm 管理、自動啟動、vCPU、記憶體與位址。`--ips` 的資料來源請見[查詢 VM 位址](networking.zh-TW.md#查詢-vm-位址)。

## status

```bash
sudo kvm-vm status web01
```

顯示 libvirt domain 資訊、kvm-vm 狀態清單、快照狀態、磁碟、網卡與已知位址。

## console

```bash
sudo kvm-vm console web01
```

連上序列主控台，按 `Ctrl+]` 離開。連上主控台不會解鎖任何密碼；若要在主控台登入，請見 [Console 密碼登入](access.zh-TW.md#console-密碼登入)。

## reinstall

Reinstall 以全新的雲端映像檔取代 VM 的系統磁碟。**系統磁碟上的所有資料都會消失**。你另外掛載的資料磁碟會維持掛載且不受影響。

```bash
sudo kvm-vm reinstall web01                     # 沿用原本的作業系統
sudo kvm-vm reinstall web01 --os debian13       # 換成另一個作業系統
sudo kvm-vm reinstall web01 --os rocky9 --force --yes   # 腳本中、VM 執行中
```

沿用已儲存定義檔的項目：vCPU、記憶體、磁碟大小、MAC 位址、IP 設定、cloud-init 使用者與 SSH 金鑰、SSH 連接埠、Console 密碼設定，以及網路連接方式（橋接、NAT 網路或 Macvtap parent）。環境變數覆寫會被忽略。

會改變的項目：guest 會產生新的 SSH host key，SSH 用戶端會警告金鑰已變更（可用 `ssh-keygen -R <vm-ip>` 移除舊紀錄）。若開啟了 Console 密碼登入，必須提供並會產生新密碼，舊密碼失效。

| 選項 | 意義 |
|---|---|
| `--os <alias\|url\|path>` | [內建別名](configuration.zh-TW.md#內建別名)、映像檔 URL 或本機映像檔路徑。 |
| `--image-url <url>` | 使用此映像檔 URL。 |
| `--image-path <path>` | 使用此本機映像檔。 |
| `--os-variant <variant>` | 指定 `virt-install` 使用的 libosinfo variant。 |
| `--refresh-image` | 即使已快取也重新下載映像檔。 |
| `--console-password-file <path>` | 開啟 Console 密碼登入時必填。新密碼會在 VM 關機或替換磁碟之前寫入。 |
| `--no-start`、`--dry-run`、`--yes`、`--force` | 詳見[開始之前](#開始之前)。 |

`--os`、`--image-url`、`--image-path` 只能擇一使用。

`--dry-run` 會檢查定義檔、網路、SSH 金鑰與憑證路徑。快照、checkpoint 與磁碟相依性檢查只在實際 reinstall 時執行。

若 reinstall 被中斷，請見 [Interrupted reinstall](lifecycle-recovery-safety.md#interrupted-reinstall)（英文）。

## clone

Clone 把一台已關機 VM 的系統磁碟複製成一台身分獨立的新 VM。

```bash
sudo virsh shutdown web01
sudo virsh domstate web01            # 等到顯示 "shut off"
kvm-vm validate --clone web02.yaml
sudo kvm-vm clone web01 web02.yaml
```

目標定義檔的寫法請參考 [`examples/clone-target.yaml`](../examples/clone-target.yaml)。它沒有 `storage.image`，因為來源磁碟就是映像檔。省略 `disk_gib` 時，新 VM 沿用來源磁碟大小。

處理流程：

```text
來源系統磁碟
      |
      +-- qemu-img convert --> 新的獨立 qcow2
                                  |
                                  +-- virt-sysprep，只執行選定的操作：
                                  |     machine-id, ssh-hostkeys, net-hostname,
                                  |     net-hwaddr, dhcp-client-state,
                                  |     udev-persistent-net, lvm-system-devices,
                                  |     random-seed
                                  |
                                  +-- cloud-init clean
                                  |
                                  +-- 新的 MAC 位址與 cloud-init instance ID
                                  |
                                  +-- 套用目標 YAML 的網路、使用者與 SSH 設定
```

kvm-vm 不會執行 `virt-sysprep` 的預設操作集，因此 guest 內的使用者帳號與應用程式資料都會保留。來源 VM 不會被修改。

Clone 會沿用來源的 OS variant，讓 `virt-install` 給它相同的虛擬硬體預設值。若要使用其他 variant（例如來源 guest 曾原地升級），請在目標定義檔設定 `vm.os_variant`。詳見 [os_variant](configuration.zh-TW.md#os_variant)。`clone --dry-run` 會顯示沿用的值；`validate --clone` 不知道來源是哪台 VM，因此無法顯示。

限制：

- 僅支援 Linux guest（`virt-sysprep` 的限制）。
- 來源 VM 必須關機。
- 來源系統磁碟必須是獨立的 qcow2 或 raw 檔案。
- 只複製系統磁碟，不複製資料磁碟。
- 目標磁碟不可小於來源。可以更大，但需由 guest 的 cloud-init 或工具擴充分割區與檔案系統。
- 有快照、checkpoint、backing chain 或執行中工作的 VM 會被拒絕。

Clone 的 Console 密碼請見 [Clone 與 reinstall](access.zh-TW.md#clone-與-reinstall)。

### 檢查 virt-sysprep 支援

`kvm-vm clone ... --dry-run` 會以上述操作對來源磁碟執行 `virt-sysprep --dry-run`，這是最快的檢查方式。kvm-vm 至少需要 `machine-id` 與 `ssh-hostkeys`；宿主機的 `virt-sysprep` 沒有的其他操作會被略過。

要測試還不是 VM 的映像檔：

```bash
virt-sysprep --list-operations         # 此宿主機支援的操作
virt-inspector -a /path/to/image.qcow2 # 必須能辨識作業系統與掛載點
virt-sysprep --dry-run -a /path/to/image.qcow2 \
  --operations machine-id,ssh-hostkeys,net-hostname,net-hwaddr,dhcp-client-state,udev-persistent-net,lvm-system-devices,random-seed
```

若最後一個指令沒有掛載錯誤或「無法辨識作業系統」的警告，該映像檔即可用於 clone。

## delete

```bash
sudo kvm-vm delete web01                # 要求輸入名稱確認
sudo kvm-vm delete web01 --yes          # 不提示
sudo kvm-vm delete web01 --force --yes  # 也強制關閉執行中的 VM
sudo kvm-vm delete web01 --keep-storage # 只取消定義，保留所有檔案
```

對 kvm-vm 建立的 VM，`delete` 會移除 libvirt domain，以及 kvm-vm 記錄的檔案：系統磁碟、cloud-init 檔案、生效定義檔與狀態清單。它絕不使用 `virsh undefine --remove-all-storage`，因此你之後掛載的資料磁碟永遠不會被刪除。Console 密碼檔也不會被刪除，請自行清理。

- 執行中的 VM 會被拒絕，除非加上 `--force`。
- `--keep-storage` 只移除 libvirt domain。kvm-vm 會保留磁碟、cloud-init 檔案、定義檔、狀態與 NVRAM，避免檔案在不知情下變成孤兒。
- 不是 kvm-vm 建立的 VM，只允許 `--keep-storage`。

## snapshot

快照（Snapshot）是每台 VM 一個、在高風險變更前建立的短期回復點。它與 VM 位於同一台宿主機與同一個儲存空間，因此**不是備份**。

```bash
sudo kvm-vm snapshot create web01                  # 執行中或關機皆可，不需重開機
sudo kvm-vm snapshot status web01                  # none、ready 或 needs attention
sudo kvm-vm snapshot delete web01 --yes            # 保留變更，刪除快照
sudo kvm-vm snapshot rollback web01 --yes --force  # 撤銷變更並重新開機
```

對執行中的 VM，`create` 會透過 QEMU guest agent 短暫凍結 guest 檔案系統。`--no-quiesce` 略過凍結，得到 crash-consistent 快照。Rollback 會把磁碟、VM 設定與 kvm-vm 檔案回復到快照當時，並保留 VM 的身分。

快照與增量備份絕不並用。初次使用前請先閱讀 [Snapshots](snapshots.md)（英文）：內容涵蓋已測試的宿主機、檢查項目、失敗處理、AppArmor 宿主機，以及與備份搭配的步驟順序。

## backup check

kvm-vm 本身不做備份。[Backup and Restore 流程](full-backup-restore.md)（英文）直接使用 `virtnbdbackup` 與 `virtnbdrestore`。此流程已通過一次跨主機演練（Debian 13 還原到 AlmaLinux 9.8，單一 guest），涵蓋完整備份，以及「完整＋增量」備份歷史中指定的還原點；其他 guest、應用程式與宿主機組合尚未驗證。任何還原之前，請先檢查備份目錄：

```bash
kvm-vm backup check /backups/web01
kvm-vm backup check /backups/web01 --until virtnbdbackup.2
```

`virtnbdrestore` 在還原不完整或順序錯亂的備份歷史時，仍可能回報成功。`backup check` 只讀取目錄，會拒絕這類歷史，並列出還原時會套用的 checkpoint。詳見 [ADR 0004](adr/0004-pre-restore-backup-check.md)（英文）。

## 指令拒絕執行時

`clone`、`reinstall`、`delete` 支援使用獨立 qcow2 或 raw 系統磁碟、並可另有檔案或區塊裝置資料磁碟的 VM。遇到以下情況會拒絕：

- 有 kvm-vm 快照或未完成的快照操作（請先用 `kvm-vm snapshot delete` 刪除，會保留目前資料）；
- 有 libvirt checkpoint、其他快照或 backing chain；
- libvirt 與 kvm-vm 記錄的磁碟路徑不一致；
- 有執行中的 block job 或備份工作。

拒絕時不會做任何變更，錯誤訊息會指出要檢查的項目。完整規則請見 [Lifecycle operations around recovery state](lifecycle-recovery-safety.md)（英文）。
