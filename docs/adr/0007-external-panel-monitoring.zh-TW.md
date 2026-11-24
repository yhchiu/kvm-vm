# ADR 0007：外部 VM Panel 監控整合

[English](0007-external-panel-monitoring.md)

- 狀態：整合介面決策已接受；客戶功能上線仍待實際 Panel 驗收。
- 原始基線：`b3a2a1ed`。

既有多用戶 VM Panel 負責登入、服務歸屬、ACL、Placement 歷史與 UI。Panel 是受信任服務，不是不可信任的租戶。中央提供私有 HTTPS 認證唯讀入口，Panel Backend 使用固定、版本化的 VM 查詢。保留 VictoriaMetrics Single、管理員 Grafana、Host 部署模式及 Remote Write；不新增正式常駐服務、第二套客戶資料庫、強制 Registry 同步或預先選定原生租戶後端。

服務憑證透過 query_range 可以查詢共享儲存，這是 Application-level Isolation，不是原生 Storage-level Isolation。客戶瀏覽器不可持有憑證或自行選擇 PromQL、Labels、後端 URL、租戶路徑。每次查詢須由 Panel 授權可信 Host/VM UUID、歷史歸屬及 Placement 有效區間，並限制 Lookback 與來源時間不確定性。客戶不得看到未分配資源、Host 指標及內部 Labels。上線前需確認 Writer 來源、時間邊界及實際 Backend 行為；若 Host 可偽造其他 VM 資料或 Panel 遭入侵，此設計不足以構成獨立安全邊界。

[ADR 0006](0006-central-monitoring-tenant-security-boundary.zh-TW.md) 仍約束中央/Grafana 直接客戶存取與原生租戶隔離；`customer-readiness` 維持 BLOCKED。本決策不為第三方 Panel 背書，Panel 需獨立驗收及管理員核准。保留原生租戶驗收契約，容量、隔離或每租戶保留期限／治理確有需求時才進行 Phase 6 PoC。詳見[整合契約](../integration/monitoring-api.zh-TW.md)。
