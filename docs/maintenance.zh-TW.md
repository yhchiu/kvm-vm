# Guest 維護錯峰排程

[English](maintenance.md)

多台 VM 共用 SSD/NVMe 時，同時執行 TRIM、索引更新或 log 壓縮可能造成 I/O 尖峰。本功能將指定 systemd timer 分散到一週中的不同日期及每日維護時段，不需要 Host 排程器或新增 Guest 常駐程式；運行中管理沿用 QEMU Guest Agent。

## 快速開始

在 VM 定義檔加入（套件提供的範例已啟用）：

```yaml
cloud_init:
  maintenance:
    profile: balanced
```

省略設定等同 `profile: none`，create/reinstall 不會預設調整 Guest timer；升級 kvm-vm 也不會修改運行中的 VM。

`balanced` 處理下列存在於 Guest 的 timer：

| Timer | 頻率 | Guest 當地時段 |
|---|---|---|
| `fstrim.timer` | 每週 | 01:00–06:00 |
| `logrotate.timer` | 每日 | 00:00–06:00 |
| `man-db.timer` | 每日 | 01:00–06:00 |
| `plocate-updatedb.timer` | 每日 | 01:00–06:00 |
| `e2scrub_all.timer` | 每週 | 01:00–06:00 |

APT/DNF 快取、自動安全更新及其他 timer 維持發行版原有設定，除非明確選用。Cron 及應用程式自己的排程不在本功能範圍內。

## 自訂政策

```yaml
cloud_init:
  timezone: Asia/Taipei
  maintenance:
    enabled: true
    profile: balanced
    strategy: staggered
    timers:
      fstrim.timer:
        window: "02:00-05:00"
      man-db.timer:
        enabled: false
      my-backup.timer:
        frequency: weekly
        window: "01:00-04:00"
```

| 欄位 | 說明 |
|---|---|
| `profile` | `none`（預設）、`balanced` 或 `custom`（不預設 timer）。 |
| `enabled` | `balanced`/`custom` 預設 true；`none` 為 false。false 不套用政策。 |
| `strategy` | `staggered`（預設且目前唯一支援）。 |
| `timers` | 具體的 systemd timer 名稱及其政策；可覆寫 profile 內個別欄位。 |
| `timers.<name>.enabled` | 預設 true；false 是排除套件管理，不是停用 systemd timer。 |
| `timers.<name>.frequency` | `daily` 或 `weekly`；新啟用的自訂 timer 必填。 |
| `timers.<name>.window` | `HH:MM-HH:MM`，限同日且不含結束時間；允許 `24:00` 作為結束。新啟用的自訂 timer 必填。 |

啟用的 custom 政策至少需要一個啟用的 timer。未知欄位、不合法名稱、時段會在驗證時報錯。請使用具體 unit 名稱，不使用萬用字元或未實例化的 template。本政策會取代 timer 原本的 calendar 及 monotonic 觸發條件，請只選擇適合如此改排程的工作。其他 unit 設定（包含 `Persistent`）保持不變。

## 選用：維護服務資源控制

包括 `balanced` 在內的所有 profile 均不會預設調整服務資源。可在啟用的 timer 下明確設定 `resources`：

```yaml
cloud_init:
  maintenance:
    profile: balanced
    timers:
      fstrim.timer:
        resources:
          nice: 10
          io_scheduling_class: idle
          cpu_weight: 20
          io_weight: 20
```

| 欄位 | systemd 指令 | 允許值 |
|---|---|---|
| `nice` | `Nice` | 整數 0–19；越高 CPU process 排程優先權越低。 |
| `io_scheduling_class` | `IOSchedulingClass` | `best-effort` 或 `idle`，不允許 realtime。 |
| `cpu_weight` | `CPUWeight` | 整數 1–10000；相對 CPU cgroup 權重，systemd 預設 100。 |
| `io_weight` | `IOWeight` | 整數 1–10000；相對 I/O cgroup 權重，systemd 預設 100。 |

所有欄位均為選填；省略欄位或使用 `{}` 即不管理該資源設定。排除 timer 政策也會排除其資源設定。權重不是硬性上限，高於 100 的權重可能提高相對優先權。範例採較低優先權；使用 `idle` 前應評估重要維護作業是否會過度延遲。

Guest 透過 systemctl 解析 timer 實際 `Unit=` 與 service 的 canonical `Id`，不猜測同名 service。僅支援存在、未 masked 且具體名稱由 ASCII 英數字、底線、點、`@`、連字號構成的 service。不存在或 masked 的目標會跳過；非 service 或不支援名稱會報錯。若兩個 timer 對同一個 canonical service 指定不同資源設定，會在修改 drop-in 前拒絕；相同設定可共用。未設定資源的 timer 不構成衝突，但資源優先權會影響該 service 的**所有啟動方式**，包括手動啟動或其他 timer 觸發。

生成的 `/etc/systemd/system/<service>.d/90-kvm-vm-maintenance-resources.conf` 示意：

```ini
[Service]
Nice=10
IOSchedulingClass=idle
CPUWeight=20
IOWeight=20
```

只寫入或移除專屬 drop-in 並 reload systemd，不會為套用資源設定而主動重啟、停止或啟動 service。Process 排程屬性於後續執行生效；已運行的 cgroup 權重可能較早變更。Timer 重啟及 Persistent 補跑行為維持原樣。`maintenance preview` 以 timer 名稱顯示預期 service 設定，實際目標只能在 Guest 解析；`status` 顯示實際 service drop-in 與 `Nice`、`IOSchedulingClass`、`CPUWeight`、`IOWeight`、`DropInPaths` 屬性。套用不含 resources 的政策（或 `maintenance remove`）會移除不再需要的 service drop-in；clone 也會離線清理兩種專屬 drop-in。

CPU/I/O 權重取決於 cgroup controller（這些指令尤其適用於 cgroup v2）、Guest 核心與儲存排程器。Guest I/O 優先權不保證傳遞至 Host，TRIM/discard 亦不保證受到限制。systemd 顯示設定不等於實際執行效力。仍須搭配 Host IOPS/頻寬控制並在實際儲存架構測試。參考 [systemd.exec](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html) 與 [systemd.resource-control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html)。

## 運作方式

透過版本化命名空間、VM 生效名稱、MAC 及 timer 名稱的 SHA-256 hash 選擇時段內的分鐘（每週 timer 另選擇星期幾）。生效定義檔保存名稱、MAC 與展開後的政策。相同輸入 reinstall 會維持原日期和分鐘；clone 通常採用不同識別。名稱/MAC 或 hash slot 仍可能碰撞，並非保證互斥的排程器或 I/O admission control。

生成的 drop-in 示意：

```ini
[Timer]
OnCalendar=
OnCalendar=Wed *-*-* 04:36:00
RandomizedDelaySec=15min
AccuracySec=1s
```

隨機延遲最多 15 分鐘（狹窄時段會縮短），並預留一秒精度，使一般 calendar 排程落在指定時段。排程依 Guest 當地時區執行；關機、時鐘/DST 變動、服務延誤、Persistent 補跑或其他管理員及發行版 drop-in 仍可能改變實際時間。

Cloud-init 會在使用者命令與 kvm-vm 初始化操作之後追加首次開機的 `runcmd`。檢查 systemd LoadState、跳過不存在及 masked 的 unit，寫入 `/etc/systemd/system/<timer>.d/90-kvm-vm-maintenance.conf` 及選用的專屬 service drop-in，再 reload systemd 並對有變更的 timer 執行 `try-restart`。不啟用、不啟動未運行的 timer；其他 drop-in 保留。檔案未變更不 reload。可由 cloud-init log 查看結果；非預期的 systemctl 或檔案系統錯誤會使維護步驟失敗。

## VM 生命週期

| 操作 | 行為 |
|---|---|
| `validate` | 離線驗證及展開政策。 |
| `create` | 產生 seed；Guest 首次開機套用。 |
| `clone` | 離線 sysprep 時移除複製磁碟中套件管理的維護 drop-in（即使目標為 `none`）；獨立產生目標政策。 |
| `reinstall` | 依生效定義檔產生新系統磁碟的政策。 |
| Backup restore / snapshot rollback | 保留被備份或快照捕獲的 Guest 檔案及設定。 |
| Reboot / migration | 保留既有 drop-in。 |
| `maintenance apply` / `remove` | 更新運行中的 Guest，成功後保存政策供日後 reinstall 使用。 |

Cloud-init user commands 於首次開機後期執行，原 timer 可能已經先執行，尤其 clone 或首次安裝套件時。刻意保留 Persistent timestamp，不阻止必要的補跑，因此可能在一般時段之外執行。錯峰不限制工作持續時間或並行數，仍需搭配 Host IOPS/頻寬限制及備份排程。

## Guest 內驗證

```sh
cloud-init status --wait
systemctl list-timers --all
systemctl cat fstrim.timer
systemctl show fstrim.timer -p TimersCalendar -p RandomizedDelayUSec -p AccuracyUSec -p Persistent -p NextElapseUSecRealtime
journalctl -u fstrim.service --since '30 days ago'
```

查看 `/var/log/cloud-init-output.log` 的 `kvm-vm maintenance:` 訊息。只修改已保存的 YAML 不會直接套用到既有 Guest。

## 從 Host 管理既有 VM

```sh
sudo kvm-vm maintenance status web01
sudo kvm-vm maintenance preview web01 --profile balanced
sudo kvm-vm maintenance apply web01 --profile balanced --dry-run
sudo kvm-vm maintenance apply web01 --profile balanced --yes
sudo kvm-vm maintenance remove web01 --yes
```

- `preview` 顯示預期政策與生成的 drop-in，不連線 Guest；關機中的受管 VM 也可預覽。
- `status` 顯示已保存政策、Guest 所有 systemd timer，以及預期管理或曾經管理的 timer/service 的實際 drop-in 與生效屬性。請比較預期與生效值；其他 drop-in 可能覆寫。此指令不修改 Guest 或有效定義檔。
- `apply` 取代受管政策（未指定選項時重新套用已保存政策），清除不再管理的專用 drop-in，設定存在且未 masked 的 timer。`remove` 將政策改為 `profile: none`。兩者不會停用 timer、啟用原本未運行的 timer 或刪除其他管理員的 drop-in。運行中套用會 reload systemd 並對受影響的 timer 執行 `try-restart`（包含內容未變更的受選 timer），以便重試先前 reload 失敗的操作。

自訂政策使用獨立 YAML mapping，而非完整 VM 定義檔：

```yaml
# maintenance.yaml
profile: balanced
timers:
  fstrim.timer:
    window: "02:00-05:00"
  man-db.timer:
    enabled: false
```

```sh
sudo kvm-vm maintenance preview web01 --policy maintenance.yaml
sudo kvm-vm maintenance apply web01 --policy maintenance.yaml --yes
```

`--profile` 與 `--policy` 互斥。`--dry-run` 只在 Host 預覽，並非 Guest 連線檢查。實際變更需明確確認（`[y/N]`），自動化使用 `--yes`。變更指令沿用 Host lifecycle lock，要求受管 VM 正在運行、保存的 MAC 與實際一致、沒有進行中的 libvirt domain job，且 Guest 為 Linux systemd，QEMU Guest Agent 已啟用 `guest-exec` / `guest-exec-status`，具備 GNU `timeout` 及 `flock`（變更時需要）。不需 SSH。若 Guest 有 cloud-init，必須成功回報 `done` 或 `disabled` 才可變更；首次初始化錯誤需先排除。Guest 指令限制執行 60 秒（另給 5 秒終止時間），Host RPC 及輪詢亦有時限。Guest lock 避免重疊套用。

成功後以原子替換更新 `/etc/kvm-vm/definitions/NAME.yaml`，保留其他設定值與權限。不改寫原始輸入 YAML 或原有 cloud-init seed。一般 reboot 保留新 drop-in；reinstall 依更新後的定義檔產生新 seed。若手動執行 `cloud-init clean` 重跑舊 seed，事後需重新套用政策。Snapshot rollback 或 backup restore 返回當時捕獲的狀態。外部備份、遷移作業仍需自行協調。

Guest 檔案系統與 Host 定義檔無法形成單一原子交易。Agent 斷線、systemctl 失敗或 Host 無法保存時，指令會回傳錯誤，Guest 可能已部分變更。等待有時限的 Guest 指令結束後，執行 `status` 檢查並使用同一份明確指定的政策重試。若先前已刪除舊 drop-in 但 timer 尚未成功 restart，請檢查該 timer，必要時在 Guest 執行 `systemctl daemon-reload` 與 `systemctl try-restart NAME.timer`。保留 Persistent 政策，因此變更排程後仍可能立即補跑。這些管理指令不應直接暴露為租戶 API。

### 實機驗收清單

使用可拋棄的運行中受管 VM，等待 cloud-init 完成並確認 Guest Agent 可用。記錄 `systemctl list-timers --all` 及管理員自訂的 `fstrim.timer.d` 檔案。先預覽再套用 balanced，以 `status` 比較 Guest 內 `systemctl cat fstrim.timer` 與 `systemctl show fstrim.timer`。重新開機確認 drop-in 保留。套用較窄時段確認排程更新，最後移除政策，驗證僅專用 drop-in 被移除，timer 原本的啟用狀態不變。至少在一台 Debian/Ubuntu 與一台 RHEL 系 Guest 執行。勿以正式環境 VM 作為首次驗收對象。

資源控制驗收：另套用上述 resources 範例，比對 `status` 與 Guest 內 `systemctl cat fstrim.service`、`systemctl show fstrim.service -p Nice -p IOSchedulingClass -p CPUWeight -p IOWeight`，確認套用不會主動重啟 service。再套用未指定 resources 的 balanced，驗證專屬 service drop-in 移除。檢查 cgroup controller 並在代表性負載下量測維護時間及 Host I/O latency。
