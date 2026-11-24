# 共用租戶安全驗收（Phase 5）

英文 [完整操作契約](README.md) 為 fixture/controller API 的規格。
本批只新增測試引擎與實際 HTTP adapter，沒有導入正式租戶路由、沒有選定後端，
現有共享 VictoriaMetrics Single 及 Grafana 仍限管理員，customer-readiness 保持 BLOCKED。
既有 tenant_handoff、managed_writer 及 resilience drills 保留不變。

離線驗證：`python3 -m pytest tests/test_tenant_security_contract.py -q`。
測試包含故意存在安全漏洞的替身；測試替身通過不代表真實 Backend 通過。

Phase 6 在隔離環境準備實際 write/read gateway、A/B/internal 三個獨立的管理員
storage oracle，及可執行的 fixture controller。依 `fixture.example.json` 建立 0600
設定檔與獨立憑證檔，替換成真實測試網址、攻擊路徑與受信任 CA。僅在確認沒有生產
資源後才設定 `disposable=true`。不可使用 `--insecure` 或將密碼放在 CLI 參數。

```sh
python3 tests/tenancy/run.py --fixture /tmp/tenant-fixture.json \
  --allow-disposable-fixture --timeout 30 --quiet 10 \
  --report /tmp/tenant-contract-result.json
```

退出碼：0 所有案例 PASS；1 FAIL；2 BLOCKED／驗收不完整。相依案例未執行標記
NOT_RUN；報告不包含密碼及網址。必須提供獨立真實儲存查詢，不得用租戶查詢回傳空值
取代管理端確認，也不得將測試替身當成實機證據。以實測佇列延遲設定觀察時間。

共用案例包含同 Host 混合租戶批次、同名 VM、任意 PromQL、instant/range/series/
labels API、查詢快取、偽造 VM/Host/Tenant、路徑/Header/參數繞過、讀寫權限分離、
移交前後的歷史樣本與延遲寫入、授權政策不可用時 fail-closed、Writer 輪替及 Writer/Reader 撤銷，
以及最後的跨分區原始樣本覆核。完整 controller action、credentials 格式、攻擊矩陣
請見英文規格。

目前尚未提供三種後端的完整實機 provisioner（Phase 6）。HTTP adapter 已能對接已經
部署的候選系統；候選系統仍須實作 VM-level 寫入授權與租戶查詢隔離。不要為了使測試
通過而讓 oracle 使用測試期望值生成結果。延遲樣本測試不等同實際 WAL/Queue 補傳，
另執行既有 E2E drills。正式客戶開放仍需 Grafana/網路繞過、憑證過期、真實
佇列、容量、備份／還原等候選架構的驗收。
