# 中央 Tenant 與 VM 歸屬 Registry

Registry 是管理員維護的 metadata，**不是授權機制**。中央維持管理員專用，customer-readiness 仍為 BLOCKED。不依賴特定 TSDB，不修改舊 Host Inventory，Host 的 tenant_id 不會自動繼承到 VM。支援同 Host 不同租戶與同租戶跨 Host。

先用 inventory 登錄 Host，再使用真實 UUID（由 virsh domuuid 或 monitor --json 取得），以下 UUID 為占位符：

```sh
sudo ./tools/setup-central-monitoring.sh registry tenant-add cust-a 'Customer A'
sudo ./tools/setup-central-monitoring.sh registry vm-add VM_UUID HOST_UUID web01 --tenant cust-a
sudo ./tools/setup-central-monitoring.sh registry list
sudo ./tools/setup-central-monitoring.sh registry vm-move VM_UUID OTHER_HOST_UUID
sudo ./tools/setup-central-monitoring.sh registry vm-at VM_UUID --at 1795500000
sudo ./tools/setup-central-monitoring.sh registry vm-unassign VM_UUID
sudo ./tools/setup-central-monitoring.sh registry vm-retire VM_UUID
```

另支援 tenant-enable、tenant-disable、vm-assign、vm-rename；詳見 `registry --help` 或[英文指令文件](monitoring-registry.md)。未分配 VM 屬保留識別 internal。停用租戶前須解除其所有有效 VM 登錄的歸屬；退休後不能重用 UUID。改名只改顯示名稱，move 只記錄位置，不操作 libvirt。

登錄、移動、移交、解除歸屬及退休可用 `--at UNIX_SECONDS` 指定 UTC 生效秒數（預設中央時間，不可指定未來）。區間 `[start,end)`；異動須晚於目前區間起點，同秒連續異動須等下一秒。僅在確認歷史紀錄後回填時間。vm-at 查不到區間回傳 JSON null，不代表授權。Host Inventory 與 Registry 為不同檔案，移除 Host 前需人工核對 VM；歷史 Host ID 保留。

資料位於 `/etc/kvm-vm/central/registry/ownership.json`（0600、schema v1、目錄 0700）。寫入採獨立 flock、原子替換與 fsync；不支援版本或損毀檔案拒絕操作，不會重設。部署與模板同步不覆寫資料；中央設定備份須涵蓋該目錄，備份時暫停管理異動。不得在操作期間手改檔案或移除鎖。

此功能不會更改 VM、憑證、Remote Write 路徑或歷史指標；未來須完成可信 Host 身分、依採樣時間驗證 VM 歸屬（含補傳）、租戶儲存及查詢隔離，才能開放客戶。
