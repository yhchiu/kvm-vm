# Guest 維護錯峰排程

[English](maintenance.md)

多台 VM 共用 SSD/NVMe 時，同時執行 TRIM、索引更新或 log 壓縮可能造成 I/O 尖峰。本功能將指定 systemd timer 分散到一週中的不同日期及每日維護時段，不需要 Host 排程器、Guest 常駐程式或額外套件。

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

Cloud-init 會在使用者命令與 kvm-vm 初始化操作之後追加首次開機的 `runcmd`。檢查 systemd LoadState、跳過不存在及 masked 的 unit，只寫入 `/etc/systemd/system/<timer>.d/90-kvm-vm-maintenance.conf`，再 reload systemd 並對有變更的 timer 執行 `try-restart`。不啟用、不啟動未運行的 timer；其他 drop-in 保留。檔案未變更不 reload。可由 cloud-init log 查看結果；非預期的 systemctl 或檔案系統錯誤會使維護步驟失敗。

## VM 生命週期

| 操作 | 行為 |
|---|---|
| `validate` | 離線驗證及展開政策。 |
| `create` | 產生 seed；Guest 首次開機套用。 |
| `clone` | 離線 sysprep 時移除複製磁碟中套件管理的維護 drop-in（即使目標為 `none`）；獨立產生目標政策。 |
| `reinstall` | 依生效定義檔產生新系統磁碟的政策。 |
| Backup restore / snapshot rollback | 保留被備份或快照捕獲的 Guest 檔案及設定。 |
| Reboot / migration | 保留既有 drop-in。 |

Cloud-init user commands 於首次開機後期執行，原 timer 可能已經先執行，尤其 clone 或首次安裝套件時。刻意保留 Persistent timestamp，不阻止必要的補跑，因此可能在一般時段之外執行。錯峰不限制工作持續時間或並行數，仍需搭配 Host IOPS/頻寬限制及備份排程。

## Guest 內驗證

```sh
cloud-init status --wait
systemctl list-timers --all
systemctl cat fstrim.timer
systemctl show fstrim.timer -p TimersCalendar -p RandomizedDelayUSec -p AccuracyUSec -p Persistent -p NextElapseUSecRealtime
journalctl -u fstrim.service --since '30 days ago'
```

查看 `/var/log/cloud-init-output.log` 的 `kvm-vm maintenance:` 訊息。只修改已保存的 YAML 不會套用到既有 Guest；目前可手動管理上述專用 drop-in，未來可加入既有 VM 的維護管理指令。移除專用 drop-in 後 reload systemd 並 try-restart 對應 timer，勿刪除其他管理員的 drop-in 或停用維護服務。
