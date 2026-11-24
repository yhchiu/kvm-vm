# 監控識別碼查詢（CLI contract v1）

在每台 KVM Host 更新既有 CLI，透過可信任的管理通道執行，不需要新增服務或資源採樣。

```sh
sudo kvm-vm identity
sudo kvm-vm identity --json
```

JSON 範例（UUID 為示意值）：

```json
{
  "schema_version": 1,
  "host_id": "11111111-1111-4111-8111-111111111111",
  "host": "kvm-tpe-01",
  "site": "taipei",
  "domains": [
    {"name": "web01", "vm_id": "22222222-2222-4222-8222-222222222222"}
  ]
}
```

- `host_id`、`host`、`site` 來自已保存的 `/etc/kvm-vm/monitoring/remote.json`，不由
  machine-id、硬體 UUID、Hostname 或環境變數推算。若使用 `KVM_VM_MONITORING_DIR`，
  查詢時也須使用相同設定。不讀密碼檔、不輸出 Writer 帳號或 URL、不建立新 UUID。
- 沒有 `remote.json`（例如僅本機監控或尚未部署 Agent／Hybrid）時，三個 Host 欄位均為
  `null`，仍可查詢 VM。已部署但停止的 Writer 保留原識別碼。Host ID 為 null 時不可啟用中央查詢。
- `domains` 包含本機 libvirt 可見的所有 VM（含關機及非本套件管理的 VM），依名稱排序。
  無 VM 時為 `[]`。使用單次 `virsh list --all --uuid --name` 取得原生 UUID；libvirt ACL
  可能限制清單，請使用具適當權限的管理通道。
- 僅允許標準 Exporter 使用的本機 `qemu:///system`，避免將遠端 VM UUID 誤配到本機。
  Panel 可透過既有可信 SSH 管理通道逐台執行；客製 Exporter 的連線需另行核對。

退出碼 0 代表成功取得快照，不代表 Writer 在線、已登錄或已有中央歷史資料。若設定檔
無法讀取、格式損毀、為 symlink 或非一般檔案，或 libvirt 失敗、逾時、UUID 無效或重複，
退出碼為 1；錯誤訊息走 stderr，不輸出部分 JSON。libvirt 查詢逾時 10 秒，Panel 仍需設定
整體 SSH/process timeout。

Panel 應驗證版本、退出碼、UUID 與預期 Host 映射。保存監控 Host ID、Domain UUID，與
Service ID 及歷史歸屬分開；不可因快照缺少 VM 或識別碼變更就自動移轉歸屬、退休 VM 或
覆寫歷史。Clone Host 不可沿用來源 Host 的監控設定。識別快照不是授權證明或原子生命週期
快照，不可直接暴露給客戶。歷史查詢請見 [監控 API 整合契約](monitoring-api.zh-TW.md)。
