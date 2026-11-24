# ADR 0006：中央監控的客戶租戶安全邊界

- **狀態：** 架構決策已接受；客戶存取仍禁止，待實作與驗證。
- **日期：** 2026-11-16

## 決策

保留現有 `local` / `agent` / `hybrid`、跨 NAT 出站 HTTPS Remote Write，
目前 VictoriaMetrics **Single** + Grafana 只供可信任管理員使用。
未實作安全的租戶資料隔離前，**不可開放 Hosting 客戶登入共享中央 Grafana**。

中央 Host Inventory 可使用 `tenant_id` 表示管理員指定的歸屬，舊資料預設
`internal`。這是**管理用 metadata，不是認證或資料查詢權限**。獨立指標
`kvm_vm_host_tenant_info{host_id,tenant_id}` 只用於中央內部查核；原本
`kvm_vm_host_expected` 不改標籤，維持告警相容性。

`customer-readiness` 預檢目前會明確回報 `BLOCKED` 並以非零碼結束，
絕不根據 Grafana 篩選器或 inventory 宣稱客戶安全隔離已完成。

## 威脅模型

- 已通過 HTTP Basic Auth 的 Host 仍可上傳任意 metrics label、偽造別台
  Host 的 `host_id` 或 `tenant_id`。不能把收到的 label 視為可信身分。
- Grafana 的 Host/VM 變數、資料夾或 Dashboard 篩選都不是授權機制。
- Inventory 的 `disable`/`remove` 不會撤銷 Remote Write 身分憑證。
- 客戶不應取得中央原始查詢 API 或可繞過租戶限制的 Datasource。

## 未來客戶登入的必要門檻

1. 建立儲存／查詢層真正的租戶分離，例如 VictoriaMetrics **Cluster** 的
   tenant-specific `vminsert`/`vmselect` 路徑、Grafana Mimir 或每租戶獨立後端。
   待共用驗收測試與實測評估後才定案。
2. 每台 Host 的驗證憑證由中央對應到 `host_id`。同一台 Host 可有多位租戶的 VM；
   **服務端**須驗證 VM UUID 及採樣時間的歸屬，再決定寫入 Tenant。未分配 VM 與
   Host 指標只供管理員使用；不得相信來源自行宣告的 `host_id` 或 `tenant_id`。
3. 客戶 Grafana 的 datasource 必須由後端強制限定租戶，即使使用者自行送
   PromQL、列舉 Labels、呼叫 API、匯入 Dashboard 也不能取得他人資料。
4. 管理員與客戶帳號／查詢權限分開；禁用可跨租戶查詢的無保護 API。
5. 在開放前完成雙租戶負面測試：偽造標籤、錯誤憑證、跨租戶查詢、
   API bypass、Token 過期／撤銷，以及資料移轉測試。

這份 ADR **不代表** 客戶登入、真正 Tenant Isolation 已開發完成。
現階段保留較簡單的 Single 儲存；客戶存取需求啟用前再選定隔離後端
與驗證流程，避免為純內部監控過度設計。

受信任第三方 Panel 服務整合請見 [ADR 0007](0007-external-panel-monitoring.md)，不放寬中央客戶直接存取門檻。
