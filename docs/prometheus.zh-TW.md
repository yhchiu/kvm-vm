# Prometheus 指標輸出（Exporter）

`kvm-vm exporter` 會以 Prometheus text exposition 0.0.4 格式輸出**唯讀**的
libvirt VM 資源資訊，沿用既有 `monitor` 的統計來源。無須在 VM 裡安裝 Agent，
也無須新增 Python 相依套件、資料庫或預設啟動的背景服務。

## 快速使用

```sh
sudo kvm-vm exporter --managed
curl -fsS http://127.0.0.1:9179/metrics
curl -fsS http://127.0.0.1:9179/healthz
```

`--managed` 只包含由 kvm-vm 管理且具有 state manifest 的 VM；不加參數則包含
宿主機上所有 libvirt domains。也可以傳入單一 VM 名稱。Exporter 會在背景定時
採樣並快取結果，Prometheus 抓取 HTTP endpoint 時**不會**再次執行 `virsh`。

```text
kvm-vm exporter [VM] [--managed] [--listen IPV4] [--port PORT]
                [--interval SECONDS] [--timeout SECONDS]
```

| 參數 | 預設值 | 說明 |
| --- | --- | --- |
| `--listen` | `127.0.0.1` | IPv4 監聽位址；預設僅本機 |
| `--port` | `9179` | HTTP 埠號 |
| `--interval` | `5` | 背景採樣間隔，0.5–3600 秒 |
| `--timeout` | `10` | 整次採樣的 `virsh` 查詢逾時，0.1–120 秒 |
| `--managed` | 不啟用 | 僅輸出有管理狀態檔的 VM |
| `VM` | 全部 | 可選，指定單一 VM |

`/metrics` 一律回傳 HTTP 200，並提供 `kvm_vm_collector_success`（1/0）
表示最後一次 libvirt 收集成功與否。收集失敗時不會輸出過期的 VM 數據，背景
會自動重試；`/healthz` 則在最近一次收集失敗時回傳 HTTP 503。
`kvm_vm_collector_last_success_timestamp_seconds` 可用來監視採樣是否停止更新。

## 指標

所有 VM 指標都有 `vm`（名稱）與 `vm_id`（libvirt Domain UUID）label；磁碟增加 `disk`，網路增加 `interface`。
若資料不可用就不輸出該指標，不會把未知值當成 0。

| 指標 | 類型 | 意義 |
| --- | --- | --- |
| `kvm_vm_collector_success` | Gauge | 最後一次收集成功（1）或失敗（0） |
| `kvm_vm_collector_duration_seconds` | Gauge | 最近一次收集時間 |
| `kvm_vm_collector_last_success_timestamp_seconds` | Gauge | 最近成功時間；尚未成功為 0 |
| `kvm_vm_domain_state{vm,state}` | Gauge | VM 目前狀態，數值為 1 |
| `kvm_vm_domain_vcpus` | Gauge | VM vCPU 數量 |
| `kvm_vm_domain_cpu_time_seconds_total` | Counter | 累積 CPU 秒數（跨全部 vCPU） |
| `kvm_vm_domain_cpu_used_cores` | Gauge | 採樣期間使用核心數（1 = 一顆核心滿載） |
| `kvm_vm_domain_cpu_usage_ratio` | Gauge | 相對於該 VM vCPU 容量的使用率（1 = 100%） |
| `kvm_vm_domain_memory_assigned_bytes` | Gauge | 已分配記憶體 |
| `kvm_vm_domain_memory_guest_used_bytes` | Gauge | Guest 估計不可回收記憶體，僅新鮮資料 |
| `kvm_vm_domain_memory_rss_bytes` | Gauge | Host 上 QEMU RSS，**不是** Guest 實際使用量 |
| `kvm_vm_domain_disk_capacity_bytes{vm,disk}` | Gauge | 磁碟來源邏輯大小 |
| `kvm_vm_domain_disk_physical_bytes{vm,disk}` | Gauge | 磁碟來源實體大小，不含 backing chain |
| `kvm_vm_domain_disk_read_bytes_total{vm,disk}` | Counter | 累積磁碟讀取 bytes |
| `kvm_vm_domain_disk_write_bytes_total{vm,disk}` | Counter | 累積磁碟寫入 bytes |
| `kvm_vm_domain_network_receive_bytes_total{vm,interface}` | Counter | Guest 累積接收 bytes |
| `kvm_vm_domain_network_transmit_bytes_total{vm,interface}` | Counter | Guest 累積發送 bytes |

若要正確取得 Guest 記憶體使用量，必須有新鮮的 balloon 記憶體統計（詳見
[monitor 說明](commands.zh-TW.md#monitor)）。離線 VM 的 I/O Counter 不輸出。
第一次採樣即可讀取累積 Counter，但 CPU 使用率 Gauge 需要至少兩次成功採樣。
Counter 在 VM 重啟後可能歸零，應由 PromQL `rate()` 處理重設。

## Prometheus 設定

將 [examples/prometheus-kvm-vm.yml](../examples/prometheus-kvm-vm.yml)
中的 job 合併到 `prometheus.yml` 的 `scrape_configs`。若 Prometheus 與
Exporter 在**同一宿主機／同一網路命名空間**，可使用 `127.0.0.1:9179`。
若 Prometheus 位於別台機器、容器或 KVM VM，`127.0.0.1` 指的是 Prometheus
自己的環境；必須改為受保護的私有連線，例如透過 Tailscale，並將 exporter
`--listen` 設成適合的私有 IPv4。

**安全提醒：** Exporter 沒有內建 HTTP 驗證或 TLS，請勿將 `9179` 直接對公網
開放。VM 名稱和裝置名稱亦可能包含敏感資訊，應透過防火牆或私有網路保護。

常用 PromQL：

```promql
# VM 相對於分配 vCPU 的 CPU 使用率
rate(kvm_vm_domain_cpu_time_seconds_total[5m]) / kvm_vm_domain_vcpus

# 各 VM 的磁碟讀取／寫入 bytes/s
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_disk_read_bytes_total[5m]))
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_disk_write_bytes_total[5m]))

# 各 VM 的網路接收／發送 bytes/s
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_network_receive_bytes_total[5m]))
sum by (host_id, vm_id, vm) (rate(kvm_vm_domain_network_transmit_bytes_total[5m]))

# 收集失敗、超過 30 秒沒有更新（需依採樣間隔調整）
kvm_vm_collector_success == 0
(time() - kvm_vm_collector_last_success_timestamp_seconds) > 30
```

## 選用 systemd 服務

`install.sh` **不會**自動安裝或啟用 exporter。若需常駐監控，可套用範例
[systemd unit](../examples/kvm-vm-exporter.service)：

```sh
sudo cp examples/kvm-vm-exporter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kvm-vm-exporter
sudo systemctl status kvm-vm-exporter
```

預設以 root 執行，原因是 libvirt Unix socket 與 kvm-vm state manifest
可能需要較高權限；有條件時應優先改用具適當 libvirt 唯讀權限的專用帳號。
範例有基本 systemd sandbox 加固，但不等於 root 程序完全隔離。
停止：`sudo systemctl disable --now kvm-vm-exporter`。

歷史指標、告警與圖表交由 Prometheus/Grafana；Host 本身 CPU、RAM、磁碟
應搭配 node_exporter 等宿主機監控工具，本功能主要提供 VM 指標。

## VM 識別及升級

每次採樣在批次 `domstats` 前後執行 `virsh list --all --uuid --name`，共用逾時
預算；無法一致識別的 VM 跳過，列表失敗則整次收集失敗。這不是原子快照或授權證明。
`monitor --json` 新增 `vm_id`，表格保持相容。重啟、改名、遷移須保留 libvirt UUID
才會維持身分，不能將舊 UUID 重用於無關的 VM。

新增 Label 會建立新序列，不會回填歷史資料。`vm_id=~".*"` 包含舊序列，
`vm_id=~".+"` 只包含具 UUID 的序列。升級、改名、遷移的 rate/lookback 視窗可能
同時包含舊新序列，不可直接用於計費或假定自動去重。中央查詢依 `host_id,vm_id,vm`
分組。既有中央部署先審閱 `sync-templates` 再 `reload`；本機 Dashboard 請先備份再
手動更新 JSON 並重啟 Grafana。升級 Host CLI 後需重啟 Exporter，確認新 label。

## 新增資源指標

以下名稱均加 `kvm_vm_domain_` 前綴；磁碟及網路保留各裝置 label。

| 後綴 | 類型 | 單位 |
| --- | --- | --- |
| `disk_{read,write,flush}_operations_total` | Counter | 累積請求數 |
| `disk_{read,write,flush}_time_seconds_total` | Counter | 累積 I/O 時間，ns 轉秒 |
| `network_{receive,transmit}_packets_total` | Counter | 封包數 |
| `network_{receive,transmit}_errors_total` | Counter | 錯誤數 |
| `network_{receive,transmit}_dropped_packets_total` | Counter | 丟棄封包數 |
| `memory_guest_stats_timestamp_seconds` | Gauge | Balloon 最後更新 Unix timestamp |
| `memory_guest_stats_fresh` | Gauge | 執行中 VM 記憶體統計有效為 1，否則 0；非執行中省略 |

```promql
sum by (host_id,vm_id,vm) (rate(kvm_vm_domain_disk_read_operations_total[5m]))
rate(kvm_vm_domain_disk_read_time_seconds_total[5m]) / (rate(kvm_vm_domain_disk_read_operations_total[5m]) > 0)
```

第二式是平均讀取請求耗時，不是實體磁碟利用率或延遲百分位。零 IOPS 時不輸出。
不支援或負數 Counter 省略。JSON 裝置欄位使用原始 libvirt 單位（時間 ns），另有
read/write operations per second；VM uptime 暫緩，避免把 CPU time 誤當 uptime。
