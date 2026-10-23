[English](README.md) | 繁體中文

# 測試

| 測試 | 執行環境 | 指令 |
|---|---|---|
| [離線測試](#離線測試) | 任何機器，不需 root | `python3 -m pytest tests -q` |
| [Smoke 測試](#smoke-測試) | 任何有 bash 的機器 | `bash tests/smoke.sh` |
| [生命週期 E2E](#生命週期-e2e) | KVM 宿主機，root | `sudo bash tests/run_e2e.sh` |
| [發行版矩陣](#發行版矩陣) | KVM 宿主機，root | `sudo python3 -m pytest tests/test_distro_e2e.py -v -s` |
| [快照與復原 E2E](#快照與復原-e2e) | KVM 宿主機，root | `sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s` |
| [Macvtap 與 Console 驗收](#macvtap-與-console-驗收) | 另一台控制端＋KVM 宿主機 | 見各流程文件 |
| [備份驗收](#備份驗收) | 兩台 KVM 宿主機 | 見流程文件 |

請先安裝 `pytest`（`python3-pytest` 或 `pip install pytest`）。

## 離線測試

```bash
python3 -m pytest tests -q
```

會執行所有測試檔。需要實體 KVM 宿主機的測試，除非以 root 在具備 `/dev/kvm` 與可用 libvirt 連線的 Linux 上執行，否則會自動略過。需要 POSIX 檔案權限的測試在 Windows 上會略過。

> [!WARNING]
> 在 KVM 宿主機上以 root 執行此指令，也會執行會建立與刪除 VM 的實機測試。在宿主機上若只要跑離線測試，請加上 `-m "not e2e"`（或改用一般使用者執行）：
>
> ```bash
> sudo python3 -m pytest tests -q -m "not e2e"
> ```

## Smoke 測試

```bash
bash tests/smoke.sh
```

編譯檢查腳本、執行 `--help` 與 `--version`、在已安裝 pytest 時執行主要的離線測試檔，並驗證範例定義檔。不需要 libvirt。

## 生命週期 E2E

`run_e2e.sh` 先檢查宿主機（Linux、root、`/dev/kvm`、工具、libvirt、`default` NAT 網路），再執行 `tests/test_e2e.py`：

```bash
sudo bash tests/run_e2e.sh                          # Debian 12 guest
sudo bash tests/run_e2e.sh --distro ubuntu24.04 -v  # 其他別名；多餘參數會傳給 pytest
```

涵蓋 `--dry-run` 檢查、以 NAT 網路建立 VM、SSH 登入、主機名稱與使用者檢查、`status` 與 `list`、離線 clone、自訂 SSH 連接埠（含 clone 時重設為 22，以及新連接埠被占用時的回復）、`reinstall --force --yes`、`delete --keep-storage`，以及完整刪除與儲存空間清理。

需求：具備 `/dev/kvm` 的 Linux 宿主機、root、執行中的 libvirt 服務與 `default` NAT 網路，以及 `virsh`、`virt-install`、`qemu-img`、`virt-sysprep`、`ssh`、`ssh-keygen` 與 cloud-init seed 工具。

SSH 最多等待 180 秒。Rocky Linux 與 AlmaLinux 會在首次開機時安裝 SELinux 工具，在較慢的宿主機上可能需要更久：

```bash
sudo KVM_VM_E2E_SSH_TIMEOUT=600 bash tests/run_e2e.sh --distro rocky8 -v
```

## 發行版矩陣

為每個別名建立一台 VM（2 vCPU、2048 MiB、20 GiB、NAT、SSH 連接埠 2222），檢查金鑰登入、實際作業系統版本，以及 cloud-init 是否完成。Rocky 與 Alma 會為該連接埠設定 SELinux 及已啟動的 firewalld。即使測試失敗也會移除 VM。

```bash
sudo python3 -m pytest tests/test_distro_e2e.py -v -s
sudo KVM_VM_E2E_DISTROS=debian13,rocky9 KVM_VM_E2E_SSH_TIMEOUT=900 \
  python3 -m pytest tests/test_distro_e2e.py -v -s
```

`KVM_VM_E2E_DISTROS` 選擇別名（預設：全部）。`KVM_VM_E2E_SSH_TIMEOUT` 設定等待時間（預設 600 秒）。

宿主機 CPU 必須符合映像檔需求：Rocky Linux 與 AlmaLinux 9 需要 x86-64-v2，其 10 版映像檔需要 x86-64-v3。CPU 太舊造成的逾時屬於檢查失敗，不算通過。目前結果請見 [REAL_MACHINE_RESULTS.md](REAL_MACHINE_RESULTS.md)（英文）。

## 快照與復原 E2E

```bash
sudo python3 -m pytest tests/test_snapshot_e2e.py -v -s
sudo python3 -m pytest tests/test_recovery_e2e.py -v
```

`test_snapshot_e2e.py` 會建立一台 `e2e-snap-*` guest（`KVM_VM_E2E_DISTRO`，預設 `debian12`），檢查快照的 create、status、delete、rollback 以及與其他生命週期指令的互動。輸出中的 `SNAPSHOT_*` 行即為量測證據。在新宿主機上依賴快照之前請先執行它，詳見 [Tested host configurations](../docs/snapshots.md#tested-host-configurations)（英文）。

快照測試會遵循 `KVM_VM_*` 路徑變數，因此可使用獨立目錄，與正式 VM 並存執行。

`test_recovery_e2e.py` 檢查生命週期指令遇到 checkpoint、backing chain 與相依映像檔時會拒絕且不做任何變更，以及 `reinstall` 與 `delete` 在有資料磁碟時能正常運作。它使用自己的暫存目錄，定義 VM 但不開機。它不能證明還原可行。

## Macvtap 與 Console 驗收

以下為需明確啟用的流程，從另一台控制端機器操作實體 KVM 宿主機：

- [MACVTAP_ACCEPTANCE.md](MACVTAP_ACCEPTANCE.md)（英文）：Macvtap 網路，從 LAN 上的用戶端檢查。
- [CONSOLE_ACCEPTANCE.md](CONSOLE_ACCEPTANCE.md)（英文）：Console 密碼登入。控制端另需 Paramiko，宿主機另需 pexpect；兩者都不是 kvm-vm 的相依套件。

## 備份驗收

[docs/full-backup-acceptance.md](../docs/full-backup-acceptance.md)（英文）說明雙宿主機的備份與還原演練，會用到本目錄中的 `prepare_*.py` 與 `restore_recovery_point.py`。
