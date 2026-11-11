[English](bridge-host.md) | 繁體中文

# 宿主機 bridge 設定

`kvm-vm` 只會把 VM 接到已經存在的 Linux bridge。它不會建立 bridge，也不會搬移宿主機位址。`kvm-vm-bridge` 是選用工具，只做這一件事：在 uplink 上建立 bridge，把宿主機位址與預設路由移到 bridge 上；若你沒有確認，就自動還原。

只在你有退路時使用：下面的計時器、帶外主控台，或是你可以重灌的主機。唯一的管理介面若改錯，SSH 會中斷，直到計時器把網路還原。

## 需求

- Linux，以 root 執行
- `ip`（iproute2）
- 用 `systemd-run` 當還原計時器。沒有它時，工具改用脫離終端的 `nohup` 行程。這個備援擋不住「父 systemd 單元結束時殺掉整個 control group」，所以請優先使用 `systemd-run`。
- 選用：`ping`（變更後檢查原來的 gateway）
- 選用：NetworkManager 要持久化時需要 `nmcli`；偵測網路服務需要 `systemctl`

| 後端 | 常見宿主機 | 偵測方式 |
|---|---|---|
| ifupdown | Debian、Ubuntu（`networking.service`） | `/etc/network/interfaces` 提到 uplink |
| NetworkManager | AlmaLinux、RHEL、Fedora | `nmcli`，且 uplink 由 NetworkManager 管理 |
| systemd-networkd | 部分 Debian、Ubuntu 伺服器 | `systemd-networkd` 加上相符的 `.network` 檔 |
| 僅執行時期 | 其他情況 | 執行時期變更仍可用。`confirm` 不會寫持久設定。 |

## 安裝

`sudo ./install.sh` 會安裝 `/usr/local/sbin/kvm-vm` 與 `/usr/local/sbin/kvm-vm-bridge`。若只要安裝這個工具：

```bash
sudo install -Dm0755 tools/kvm-vm-bridge /usr/local/sbin/kvm-vm-bridge
```

## 用法

```text
kvm-vm-bridge detect --uplink enp4s0
kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 120 --dry-run
kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 120
kvm-vm-bridge confirm --bridge br0
kvm-vm-bridge status [--bridge br0]
kvm-vm-bridge rollback --bridge br0 --yes
```

| 選項 | 意義 |
|---|---|
| `--uplink` | 目前持有宿主機位址的介面 |
| `--bridge` | 要建立的 bridge（預設 `br0`） |
| `--timeout` | 等待 `confirm` 的秒數，逾時就還原（預設 120） |
| `--dry-run` | 只印出計畫與偵測到的後端，不做變更 |
| `--no-persist` | confirm 時取消計時器，但不寫持久設定 |
| `--yes` | `rollback` 必須加上 |

在 uplink 只有 `enp4s0` 的 Debian ifupdown 宿主機上：

```bash
sudo kvm-vm-bridge detect --uplink enp4s0
sudo kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 180 --dry-run
sudo kvm-vm-bridge create --uplink enp4s0 --bridge br0 --timeout 180
# 這個 SSH 工作階段還在的話：
sudo kvm-vm-bridge confirm --bridge br0
```

若 SSH 中斷，請等待逾時。宿主機應會自行把位址放回 `enp4s0`。重新連線後閱讀 `/var/log/kvm-vm-bridge.log`。

之後若要撤銷已確認的 bridge：

```bash
sudo kvm-vm-bridge rollback --bridge br0 --yes
```

這次 rollback 沒有第二層計時器。遠端主機請在有主控台時再還原。

## create 做什麼

1. 把位址、路由與網路設定檔快照到 `/var/lib/kvm-vm-bridge/sessions/<bridge>/`。
2. 在**任何網路變更之前**先武裝還原計時器。計時器是以 `--no-block` 啟動的 `systemd-run` oneshot，所以 `create` 不會等到逾時才返回。確認後會寫下 `CONFIRMED` 戳記，計時器結束時就不會還原。
3. 建立 bridge 時關閉 **STP**，並把 **forward_delay 設為 0**，避免 bridge 先進入約 30 秒的 listening 狀態。
4. 把 uplink 的 MAC 複製到 bridge。
5. 在 bridge 上加入宿主機位址、把 uplink 加入 bridge、從 uplink 刪除那些位址，並移轉預設路由。
6. Ping 原來的預設 gateway。失敗會立刻還原執行時期的變更。

`confirm` 會取消計時器，除非加了 `--no-persist`，否則會寫入持久設定。它不會重啟 NetworkManager、ifupdown 或 networkd，因為執行時期的設定已經就位。之後的開機才會使用新檔案。

試用視窗內不會寫持久設定。因此在 `confirm` 之前重開機，會回到原來的設定。

`rollback` 會依快照還原執行時期網路、刪除這個工具寫入的檔案或 NetworkManager 連線、在 ifupdown 的 `interfaces` 曾被修改時還原該檔，並封存 session。

自動還原的計時器也會封存 session。計時器觸發之後，`status` 不會再看到進行中的 session，同一個 bridge 名稱可以再次 `create`。已經還原的失敗 `create` 會留在 `failed/` 供檢查，不會擋住下一次 `create`。

## 日誌與狀態

| 路徑 | 內容 |
|---|---|
| `/var/log/kvm-vm-bridge.log` | 帶時間戳的日誌 |
| `/var/lib/kvm-vm-bridge/sessions/<bridge>/` | 進行中的 session：`meta.json`、快照、`rollback.sh` |
| `/var/lib/kvm-vm-bridge/archived/` | 成功 rollback 之後的 session |
| `/var/lib/kvm-vm-bridge/failed/` | 失敗且已還原的 create |

可用 `KVM_VM_BRIDGE_STATE_DIR` 與 `KVM_VM_BRIDGE_LOG` 覆寫路徑。

## 防火牆警告

`create`（含 `--dry-run`）與 `confirm` 會在「橋接訪客的 IPv4 很可能被宿主機防火牆丟掉」時提出警告。Docker 宿主機很常見：已載入 `br_netfilter`、`net.bridge.bridge-nf-call-iptables` 為 `1`，而且 iptables 的 FORWARD 政策是 DROP，ACCEPT 只放行 Docker 與 libvirt 的 bridge。

警告不會改任何規則，也不會拒絕指令。宿主機仍然可以連到同一座 bridge 上的訪客，因為那條路徑不經過 FORWARD。`kvm-vm create`、`clone` 與 `reinstall` 對已經存在的 bridge 會印出同樣的警告。`kvm-vm validate` 不檢查宿主機。該橋的 `nf_call_iptables` 為 0 不會讓警告消失：全域 sysctl 為 1 時，核心仍會過濾這座 bridge。請看[橋接訪客與宿主機防火牆](networking.zh-TW.md#橋接訪客與宿主機防火牆)。

## 安全

- 工具會拒絕不存在、未啟動、本身已是 bridge，或已經加入其他 bridge 的 uplink，也會拒絕沿用已存在的 bridge 名稱。
- 不修改防火牆規則、sysctl 或 libvirt 網路。
- 不要把 `create` 包在短命的 systemd 單元裡。單元結束時可能殺掉 `nohup` 備援計時器。`systemd-run` 計時器是獨立單元，不受影響。

## 測試

`python3 -m pytest tests/test_bridge_tool.py -q` 是離線測試，不會建立 bridge。實機 dry-run 預設略過，除非設定 `KVM_VM_BRIDGE_LIVE=1`，並用 `KVM_VM_BRIDGE_LIVE_UPLINK` 指定一張可犧牲的 uplink。不要把它指到正式主機唯一的介面。自動還原的驗收（create、不要 confirm、等待、確認 SSH 仍可用）請在可犧牲的宿主機上手工執行。
