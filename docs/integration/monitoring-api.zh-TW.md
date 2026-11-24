# 第三方 VM Panel 監控整合（契約 v1）

本階段為 Phase 5.1，基線 `b3a2a1ed`。完整 API、設定範例與驗收規範請見
[英文整合文件](monitoring-api.md)。架構決策見 [ADR 0007](../adr/0007-external-panel-monitoring.md)。

VM Panel 負責登入、ACL、服務歸屬、歷史 VM/Host 映射及圖表；kvm-vm 保持採集、
Remote Write、中央儲存、管理員 Grafana。沿用 VictoriaMetrics Single，不新增常駐
API Server 或同步客戶資料庫。中央原有 Registry 可供獨立管理員使用；Panel 整合
時以 Panel 為權威來源。客戶不直接存取 PromQL、Grafana、TSDB 或服務憑證。

## 部署

選用 `tools/monitoring/central/nginx-panel-query.example.conf`，經人工審閱後部署於
獨立私有 TLS virtual host，限制 Panel IP、獨立 Basic Auth、有效憑證及防火牆。
僅轉送 `GET /api/v1/query_range` 至中央 loopback，其他路徑／方法拒絕，不轉送
外部 Header/Body、不快取。render/up 只放置未啟用的範本，不更改既有 NGINX。
不得將服務憑證給客戶或把任意 PromQL 轉送給後端：此服務帳號可查詢共享資料。
安裝 htpasswd、權限、TLS、輪替及 nginx -t/reload 指令見英文文件。

## Panel Backend 介面

`examples/monitoring_client.py` 為 Python 3.10+ 可匯入參考模組，使用版本化
`tools/monitoring/queries/v1.json` 指標清單。支援 18 種 CPU/vCPU、RAM、Disk 容量、
讀寫流量/IOPS、Network 流量/封包/錯誤/丟棄指標。採固定 Host UUID + Domain UUID
精確比對，並去除結果的內部 Labels；不提供原始累積計數、Host 層指標、任意查詢。

```python
from examples.monitoring_client import QueryClient, PanelMonitoring

client = QueryClient(config.monitoring_url, secrets.monitoring_user,
                     secrets.monitoring_password, ca_file=config.monitoring_ca)
monitoring = PanelMonitoring(client, authorize, admit)
result = monitoring.read(session.principal, service_id, "cpu_ratio", start, end)
```

Panel 實作 `authorize(principal, service_id)`，由已驗證 session 與可信資料庫回傳
`Grant(revision, tuple[Placement, ...])` 或 None；Placement 記錄 Host/VM UUID 與 UTC
有效區間（含起點、不含結束）。`admit` 為實際查詢配額/並發控制。不得由前端傳入
principal、UUID、PromQL、後端 URL；未知歸屬或授權失敗拒絕查詢，查詢後重新授權。
回傳 `schema_version`、metric、unit、step 及 `points: [[timestamp, value_or_null],...]`；
缺值為 null，沒有資料不代表 0。CPU ratio 的 1 為 100%，流量單位 bytes/s。
預設最大 30 天、1000 點、最小 step 15 秒、16 段歷史區間；無共享快取。

## 歷史隔離及限制

VM 改名以 UUID 辨識，遷移/重建由 Panel 保存 Placement 歷史，不推測未知歷史。
每個區間獨立查詢，顯式 rate window 300 秒、gauge 60 秒，另外保留 180 秒來源時間
安全緩衝，避免查到移交前的 Rate 或快取資料。VictoriaMetrics 原生 rate 會讀取
窗口前樣本，故採 `rate_prometheus()`；真正 Prometheus 引擎才設定使用 `rate()`。
180 秒是待實機驗證的時間不確定性上限，必須核對時鐘、採樣、Exporter cache、
balloon freshness 及可信 Writer；不符合時須停用客戶查詢或調整後重新驗證。
區間邊界可能有空白；不保證長時間圖表為完整桶平均，也不適用計費。

這是 Application-level Multi-tenancy，不是原生資料庫隔離。Panel 漏洞或被入侵
的基礎設施仍可能造成洩漏；尚未完成實機 TSDB 與實際 Panel 驗收。
`customer-readiness` 仍維持 BLOCKED，不開放中央客戶登入。先按
[Panel 驗收清單](../../tests/tenancy/panel-acceptance.md) 實測後再由管理員決定是否
開放 Panel 功能。原 Phase 5 安全契約保留，Phase 6 儲存後端 PoC 延後至確有需求時。

使用 [`kvm-vm identity --json`](identity.zh-TW.md) 從每台 KVM Host 取得監控 UUID；Host UUID 為 null 時不得啟用中央查詢。識別快照不代表客戶歸屬。
