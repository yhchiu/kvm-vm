# 中央監控故障與租戶移交演練

目前的中央 Grafana、VictoriaMetrics 與 inventory 只供管理員使用。線上部署繼續使用既有 Remote Write 路徑。下列租戶入口、查詢帳號及儲存服務只在演練期間建立。[中央安全邊界 ADR](../../adr/0006-central-monitoring-tenant-security-boundary.zh-TW.md)

## 重跑條件

依雙 NAT Lab 文件執行 plan，核對資源後完成 up 與 bootstrap。從同一個控制端使用相同的 KVM_VM_E2E_STATE_ROOT，串行執行以下命令。請將 harden 換成本次 Lab ID。還原演練另需一台 4 GiB RAM、24 GiB 虛擬磁碟的 Guest，以及備份兩個資料卷的空間。[Lab 操作指南](../../../tests/e2e/README.zh-TW.md)

```sh
python3 tests/e2e/writer_recovery.py --lab-id harden
python3 tests/e2e/tenant_handoff.py --lab-id harden
python3 tests/e2e/recovery_drill.py --lab-id harden
```

三個程式成功時均將一份 JSON 寫至 stdout。最後一個程式要求目標 Guest 沒有既有中央設定、同名容器或資料卷。完成一次還原後，請以新 Lab 重跑。Guest 專用挑戰、備份、webhook URL 與憑證一律留在 Lab；只帶出驗收 JSON。

## 中斷的監控部署

若命令因另一個管理操作持有鎖而失敗，先等待該操作完成。若 Compose 啟動後安裝程序遭終止，讀取 /etc/kvm-vm/monitoring/.mode.pending。以紀錄中的模式重新執行 up，或用相同模式 down 收回這次部署。正常的模式轉換先 down 舊模式再 up 新模式，保留原有資料卷。人工刪除標記會失去復原依據。

驗收時同時核對運行中的 writer 與中央原始採樣時間。Agent 應只有 vmagent，Hybrid 應只有 Prometheus 執行 Remote Write。等待該 Host UUID 的成功採樣時間晚於切換時刻。

## Host 由 A 移交給 B

開發用註冊工具的 stage 命令接受註冊名稱、Host UUID、租戶名稱、專屬 relay 連接埠及該租戶的儲存連接埠。管理員在受保護的目錄建立由 root 擁有、nginx 可讀的空白 0640 檔案，並將檔案路徑傳入 --file。activate 和 rotate 從 stdin 接收單行密碼；list 只列綁定資訊。所有綁定更新共用鎖與同一份原子替換的認證檔。

1. 為 B 建立新的 staged 註冊與空佇列。入口按已驗證的帳號選 relay，relay 捨棄傳入的身分標籤，再加上管理員登錄的 Host 與租戶。每個租戶使用個別的 loopback VictoriaMetrics Single。
2. 停止 A 收集新資料，保留 A 的 queue、憑證、relay 與儲存目的地。僅以原註冊重啟傳送，直到 A 的 queue 歸零，且 A 的儲存端查得到斷線時間窗內的原始樣本。若不能確認補傳，保持 B 未啟用，保留 A 的 queue 供管理員處理。
3. 停止 A writer，再 revoke A；實際確認舊帳密回傳 HTTP 401。啟用 B 的註冊，以新的 queue 收集。驗證 B 有新樣本、A 歷史不變，並以各自的私有讀取帳號查詢兩個已知有資料的時間窗。
4. 同一註冊的 rotate 會換密碼，保留其綁定。跨租戶移交必須建立新註冊。inventory 的 set-tenant 只改管理員資料，沒有移交或撤銷效果。

此次私有查詢只允許管理員從 Central loopback 進入即時查詢端點。正式客戶登入、所有查詢 API、Cluster、Grafana 權限與正式遷移仍須另行驗收。

## 獨立通知與空白機還原

觀測器可在另一台受監督的主機執行。health-url 與 webhook-url 檔案各放一個 HTTPS URL。health 端點需回傳 HTTP 200 和 OK；機密 URL 與狀態目錄限服務帳號讀寫。私有 CA 使用 --ca-file，公開信任鏈可使用系統憑證庫。接收端應以 Idempotency-Key 去重；觀測器會重試尚未確認的事件。

```sh
python3 tools/monitoring/watch-central.py \
  --health-file /etc/central-observer/health-url \
  --webhook-file /etc/central-observer/webhook-url \
  --ca-file /etc/central-observer/ca.pem \
  --state /var/lib/central-observer/state.json
```

還原演練停止原中央 Compose，備份設定目錄以及實際掛載的 VictoriaMetrics、Grafana 資料卷，再啟動原服務。備份含管理資訊，經 SSH 於 Guest 間傳送；目標驗證 SHA-256、還原擁有者及資料卷。在原 Central 暫停期間，Router B 觀測，Router A 經 HTTPS 收件。新中央需同時通過固定視窗原始樣本、inventory、Grafana Dashboard UID 比對，才將健康檢查切到還原機並驗證 resolved。這次量測範圍為中央資料恢復可查；入口 DNS、憑證輪替、writer 切換及真人通知是其他驗收項目。
