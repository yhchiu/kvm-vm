# kvm-vm 中央監控（Remote Write）

採用 **Exporter → vmagent／Local Prometheus → HTTPS + Basic Auth → 中央 VictoriaMetrics Single → Grafana / vmalert / Alertmanager**。Host 可位於不同機房或 NAT 後方，不必開放入站連接埠；中央不需要安裝 libvirt。

## 部署模式

| 模式 | KVM Host 元件 | 本機歷史資料 | 上傳中央 |
|---|---|---|---|
| `local`（原本預設） | Exporter + Prometheus + Grafana | 有，預設 15 天 | 否 |
| `agent` | Exporter + vmagent | 只有待傳送資料佇列，無可查詢 TSDB | 是 |
| `hybrid` | Exporter + Prometheus + Grafana | 有，預設 15 天 | 是 |

**同一 Host 只使用一個 Remote Write 來源。** Hybrid 直接使用本機 Prometheus `remote_write`，不另外執行 vmagent，以免重複資料。

## 1. 建立中央伺服器

需求：Linux + Docker Engine + Compose v2 + Python 3；不需要 libvirt。

```bash
sudo ./tools/setup-central-monitoring.sh up
sudo ./tools/setup-central-monitoring.sh credentials
```

中央提供 VictoriaMetrics、Grafana、vmalert 與 Alertmanager。VictoriaMetrics 預設 `127.0.0.1:8428`，Grafana 預設 `127.0.0.1:3000`，**僅綁定本機**。VictoriaMetrics 在此部署未啟用內建身分驗證，絕對不可直接公開 8428 埠。資料儲存在 Docker named volumes，`down` 不會刪除。預設保留 30 天。

```bash
sudo env GRAFANA_PORT=3100 VM_PORT=8430 VM_RETENTION=60d \
  ./tools/setup-central-monitoring.sh up
```

可以用 `./tools/setup-central-monitoring.sh render DIRECTORY` 離線產生設定；重複執行不覆蓋既有 `.env`、Dashboard、告警規則。升級版本前先備份並調整保存於 `/etc/kvm-vm/central/compose.yaml` 的 image tag。

### 遠端寫入 HTTPS 入口（必要）

使用獨立的 DNS 名稱，例如 `metrics.example.com`，並用 NGINX 與有效 TLS 憑證建立 HTTPS Reverse Proxy。範例位於：

`tools/monitoring/central/nginx-remote-write.example.conf`

這個入口只允許 `POST /api/v1/write`，透過每台 Host 的獨立 Basic Auth 帳密驗證，後送到 localhost 的 VictoriaMetrics。其餘路徑返回 404，不能直接公開儲存端的其他 API。若修改 `VM_PORT`，也必須同步修改 NGINX 範例中的 `proxy_pass` 埠號。

在中央建立 Host 憑證：

```bash
sudo ./tools/setup-central-monitoring.sh enroll kvm-tpe-01
```

此指令透過短暫的 `httpd:2.4-alpine` 容器執行 `htpasswd -B`，密碼經 stdin 傳入，**僅顯示一次**，中央只保存 bcrypt hash。部署 `/etc/kvm-vm/central/htpasswd` 到 `/etc/nginx/kvm-vm/htpasswd` 並確認 NGINX worker 可讀後 reload。每次新增、撤銷或更換憑證都要同步該檔。撤銷時刪除對應使用者的 hash 並重新載入 NGINX。

Grafana 僅供內部管理員使用，建議 SSH Tunnel（`ssh -L 3000:127.0.0.1:3000 ...`）或已驗證的私有管理網路。本版**沒有**客戶租戶隔離功能。

### 未來 Hosting 客戶登入：目前只建立 Tenant 資料模型

**既有中央安裝升級注意：** 首次使用 `--tenant` 或 `set-tenant` 以前，
請先審閱並執行 `sudo ./tools/setup-central-monitoring.sh sync-templates`
（會備份並覆寫部分樣板檔），再執行
`sudo ./tools/setup-central-monitoring.sh reload`。舊版 Inventory exporter
無法讀取含 `tenant_id` 的 JSON，因此未更新時 CLI 會拒絕變更，
避免影響 Host 失聯告警。



管理員可在中央 Host Inventory 指定租戶歸屬：

```bash
sudo ./tools/setup-central-monitoring.sh inventory add <UUID> kvm-tpe-01 taipei --tenant cust-001
sudo ./tools/setup-central-monitoring.sh inventory set-tenant <UUID> cust-002
sudo ./tools/setup-central-monitoring.sh inventory list
./tools/setup-central-monitoring.sh customer-readiness  # BLOCKED / 非零退出碼
```

舊 Host 記錄不需遷移，視為 `internal`。中央 Inventory 增加
`kvm_vm_host_tenant_info{host_id,tenant_id}` 提供**管理員查核**，並不據此
開放資料查詢或授權。已經驗證過的 Remote Write 傳送端仍可能偽造
`host_id` / `tenant_id`；VictoriaMetrics Single 目前也沒有原生租戶分隔。
**中央 Grafana 在完成可信寫入路由、租戶限定查詢及雙租戶負面測試之前，
禁止讓 Hosting 客戶登入。** 詳見
[ADR 0006](adr/0006-central-monitoring-tenant-security-boundary.zh-TW.md)。

## 2. KVM Host 的 Agent 模式

Host 需 Docker Compose v2、systemd、Python 3、virsh 與可用的 libvirt。先在 Host 安全建立密碼檔：

```bash
sudo install -d -m 0700 /etc/kvm-vm
sudo sh -c 'umask 077; cat > /etc/kvm-vm/central-password'
# 輸入中央一次性提供的密碼，按 Ctrl-D 結束；勿直接寫入 Shell 歷史。

sudo env \
  KVM_VM_REMOTE_WRITE_URL=https://metrics.example.com/api/v1/write \
  KVM_VM_REMOTE_USERNAME=kvm-tpe-01 \
  KVM_VM_REMOTE_PASSWORD_FILE=/etc/kvm-vm/central-password \
  KVM_VM_MONITOR_HOST=kvm-tpe-01 \
  KVM_VM_MONITOR_SITE=taipei \
  ./tools/setup-monitoring.sh up --mode agent
```

首次安裝會產生**永久且穩定的 `host_id` UUID**，加入 metrics `host_id`、`host`、`site` labels，避免不同 Host 的同名 VM 混淆。身份保存在 `remote.json`，切換模式不可重新產生。密碼檔存於 0700 私有目錄；檔案為 0644，供 Docker 內非 root 行程透過唯讀 bind mount 讀取。密碼不會被寫進 Compose 環境變數、metrics 或命令列參數。

vmagent 每 15 秒抓取 Exporter，佇列使用 Docker named volume，預設每個目的地上限 **2 GiB**。長期斷線且佇列滿時會丟棄最舊資料，不能視為無限期保留。Exporter 自身預設每 5 秒採樣一次。

```bash
sudo ./tools/setup-monitoring.sh status --mode agent
sudo ./tools/setup-monitoring.sh logs --mode agent
sudo ./tools/setup-monitoring.sh update --mode agent
sudo ./tools/setup-monitoring.sh down --mode agent
```

## 3. Hybrid 模式

使用與 Agent 相同的 `KVM_VM_REMOTE_*` 設定：

```bash
sudo ./tools/setup-monitoring.sh up --mode hybrid
```

本機 Prometheus／Grafana 與歷史資料保留，Prometheus 新增 `remote_write` 傳送至中央，不額外啟動 vmagent。Hybrid 的 Prometheus WAL 通常無法補傳超過約 **兩小時** 的斷線期間資料；若跨機房斷線可能持續更久，建議 Agent。

原本的 `prometheus.yml` 不會被覆蓋；Hybrid 改用獨立產生的 `prometheus-hybrid.yml`。若你有自訂 scrape jobs，首次部署後可手動調整 `prometheus-hybrid.yml`；後續 `up` 會保留自訂設定。修改 `remote.json` 的識別／連線設定時，須經檢查後以 `KVM_VM_REMOTE_RECONFIGURE=1` 重新產生設定（會覆蓋自訂 scrape 檔，請先備份）。

## 4. 模式切換、身份與歷史資料

切換模式前必須先 `down`，避免同一 Host 同時上傳兩份資料：

```bash
sudo ./tools/setup-monitoring.sh down --mode agent
sudo ./tools/setup-monitoring.sh up --mode hybrid
```

`down` 不會加 `-v`，所以不刪除本機 Prometheus、Grafana 或 vmagent 佇列的 Docker volumes。`remote.json` 內 UUID 與 `remote-password` 會保留。若要改回 Local，先停止遠端模式，再執行原本的 `sudo ./tools/setup-monitoring.sh up`。

**注意：** vmagent 佇列不會自動轉移至 Prometheus WAL（反之亦然）。切換前應確認待送佇列已清空。若要調整遠端 URL／username，明確修改 `remote.json`，以 `KVM_VM_REMOTE_RECONFIGURE=1` 再執行 `up`；改密碼則需安排中央、新密碼與舊密碼交接，避免斷線。

## 5. Dashboard 與告警

中央 Grafana 自動匯入 **KVM Fleet Overview**（Hosts、Running VMs、CPU／RAM／Disk／Network）及 **KVM Fleet – Virtual Machines**（Site／Host／VM 篩選）。指標帶有 `host_id`，跨 Host 不會把同名 VM 誤當一台。

`vmalert` 已設定 Collector 失敗與 Agent Remote Write 佇列持續增加告警。**Alertmanager 暫不提供通知管道**，需要管理員編輯 `/etc/kvm-vm/central/alertmanager/alertmanager.yml` 加入 Email／Webhook 等 Receiver 才會發送通知。

**Host 完全失聯**時不會再產生新的 `up` 指標，因此要準確告警仍需建立預期在線的 Host Inventory。此外，Agent 佇列告警只有在監控資料仍能上傳時才會抵達中央；全斷線時無法即時觀察。Host Inventory 與自動憑證生命週期屬後續正式營運加強階段，並非本次已完成的功能。

## 6. 驗證與限制

Host：

```bash
curl -fsS http://127.0.0.1:9179/metrics | grep kvm_vm_collector_success
curl -fsS http://127.0.0.1:8429/metrics | head  # 僅 Agent 模式
```

中央：

```bash
curl -fsS http://127.0.0.1:8428/health
sudo ./tools/setup-central-monitoring.sh status
sudo ./tools/setup-central-monitoring.sh logs
```

確認中央可查詢 `kvm_vm_collector_success{host_id="..."}`。完整 E2E 仍需真實 KVM Host、Docker，以及有效 TLS 入口測試。VictoriaMetrics Single 並非高可用，正式使用需備份／還原演練；此外，**中央 Remote Write 在你建立 HTTPS 認證入口前不會被外部 Host 存取**，這是刻意的安全預設。

## 後續擴充：Host Inventory、失聯告警、Host 資源監控

中央端新增唯讀 Inventory Exporter（Docker 內部 9187，不對外開 Port），將 `/etc/kvm-vm/central/inventory/hosts.json` 內啟用的主機轉為 `kvm_vm_host_expected{host_id,host,site} 1`。VictoriaMetrics 每 30 秒抓取一次，`KvmVmHostOffline` 會比對最近五分鐘收到的 VM Collector 資料；另外等待五分鐘後才發出告警，因此能辨識**完全停止上報**的主機。失聯不等於斷電，也可能是網路或上報程式異常。

**Remote Write 帳號註冊與 Host Inventory 是兩個不同動作。** 在 Host 的 `/etc/kvm-vm/monitoring/remote.json` 查出 UUID，回到中央伺服器登錄：

```bash
sudo ./tools/setup-central-monitoring.sh inventory add \
  eb90c4d9-7b0e-4f4f-a4bb-9490124c85ae kvm-tpe-01 taipei
sudo ./tools/setup-central-monitoring.sh inventory list
# 預定維護期間暫停失聯告警
sudo ./tools/setup-central-monitoring.sh inventory disable eb90c4d9-7b0e-4f4f-a4bb-9490124c85ae
sudo ./tools/setup-central-monitoring.sh inventory enable eb90c4d9-7b0e-4f4f-a4bb-9490124c85ae
# 永久移除，不會自動撤銷 NGINX 帳密
sudo ./tools/setup-central-monitoring.sh inventory remove eb90c4d9-7b0e-4f4f-a4bb-9490124c85ae
```

Agent／Hybrid 預設新增 `node_exporter`（127.0.0.1:9100），使用 Host Networking、Host PID Namespace 和唯讀 Host Rootfs Mount，提供宿主機 CPU、記憶體、磁碟、網卡指標，並加入中央 **KVM Hosts Overview** Dashboard。不需要在 VM 內安裝 Agent，也不新增對外 Port。若宿主機既有 9100 服務，請先檢查連接埠衝突。

已部署舊版 Agent／Hybrid 時，為避免覆蓋自訂設定，請先備份並明確重新產生：

```bash
sudo env KVM_VM_REMOTE_RECONFIGURE=1 \
  ./tools/setup-monitoring.sh up --mode agent
# 也可指定 --mode hybrid
```

此操作會覆寫產生的 Scrape/Compose 設定檔，**不會**更換 Host UUID、遠端認證密碼或刪除既有 metrics volumes。

## 告警 Email / Webhook 通知

告警接收者預設未設定，不會寄送通知。管理員可選擇 SMTP 或 HTTPS Webhook，敏感資訊由檔案讀取，不放入 Docker Compose 的環境變數或指令參數：

```bash
# 檔案內是一整行 HTTPS Webhook URL（可含 Token）
sudo ./tools/setup-central-monitoring.sh notifications webhook /root/alert-webhook-url
# 或者設定 SMTP（密碼放入 /root/smtp-password）
sudo ./tools/setup-central-monitoring.sh notifications email \
  admin@example.com smtp.example.com:587 monitor@example.com \
  monitor@example.com /root/smtp-password
sudo ./tools/setup-central-monitoring.sh reload
```

Webhook 接收端必須支援 Alertmanager 標準 JSON payload。設定完成**不代表已確認送達**，必須在實機觸發測試告警驗證。通知設定及密碼儲存在管理員私有目錄；Docker/root 管理員仍有權讀取。

## 已部署的中央端如何升級

新安裝會包含 Inventory 和 Host Dashboard；既有安裝預設不覆蓋自訂 Compose、規則及 Dashboard，請明確執行：

```bash
sudo ./tools/setup-central-monitoring.sh sync-templates
sudo ./tools/setup-central-monitoring.sh reload
```

`sync-templates` 會先備份舊版可修改的模板，並保留 `.env`、Inventory 資料、認證設定、Alertmanager 接收者以及所有 Docker 資料 volumes；更新前請先檢查原本有無自行修改。`reload` 會短暫重建容器，確保 bind-mounted 設定生效，不清除歷史資料。

**待實機驗證：** Docker 實際啟動、跨 NAT 的 TLS Remote Write、Inventory 查詢、Host 斷線及恢復的告警、SMTP／Webhook 實際發送、Grafana 圖表。離線測試通過不代表以上端到端功能已驗證。

管理員 VM 與租戶歸屬請見 [中央 Registry](monitoring-registry.zh-TW.md)。

- [Writer 憑證生命週期、傳輸診斷、獨立觀測器與故障演練（Phase 3、4）](monitoring-security-reliability.zh-TW.md).

[第三方 VM Panel 整合（受信任服務專用；禁止中央客戶直接登入）](integration/monitoring-api.zh-TW.md).
