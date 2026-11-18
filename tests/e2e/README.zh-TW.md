# kvm-vm 中央監控：獨立 KVM/NAT E2E Lab

[English](README.md)

這是一套**需明確執行、僅對測試資源具破壞性**的整合測試環境。使用單一實體 KVM Linux 宿主機，建立中央監控 VM、兩台模擬機房的 NAT Router VM，以及兩台模擬 KVM Host VM，測試真正的 Remote Write 與監控平台，而非只使用模擬 `virsh` 輸出。

## 網路拓樸

| 測試資源 | IP / 網路 | 功能 |
|---|---|---|
| WAN | `172.30.0.0/24`（libvirt NAT） | 最外層 NAT，供中央與 Router 互通、出站下載套件 |
| Site A | `10.10.1.0/24`（isolated） | NAT Router A `10.10.1.2`、Host A `10.10.1.10` |
| Site B | `10.10.2.0/24`（isolated） | NAT Router B `10.10.2.2`、Host B `10.10.2.10` |
| Central | `172.30.0.10` | VictoriaMetrics、Grafana、vmalert、Alertmanager、NGINX HTTPS |
| Host A | Agent | kvm-vm Exporter + node_exporter + vmagent |
| Host B | Hybrid | kvm-vm Exporter + node_exporter + 本機 Prometheus/Grafana |

實體 Host 可以透過三個 libvirt bridge 管理測試 VM，中央 VM 內還會設置兩條針對 Site LAN 的 blackhole routes，避免實體 Host 的管理橋接造成旁路，因此**中央 VM 不應能直接進入兩個 Site LAN**；`connectivity` 測試會檢查此條件。Router 使用 iptables NAT，WAN 再透過 libvirt NAT 出站，形成雙層 NAT。測試不修改實體 Host 的防火牆，也不使用 libvirt `default` network。

## 前置條件

- **建議使用可任意操作的獨立實機**，Linux、root、`/dev/kvm`、`qemu:///system`。建議至少 **24 GiB RAM、80 GiB 可用硬碟**，並預留 Nested VM 的額外資源。
- 實體 Host 需有 `virsh`、`virt-install`、`qemu-img`、`cloud-localds`、`ssh`、`ssh-keygen`、`openssl`、`python3`、`ip`。
- 準備可信任的 Debian 13 或 Ubuntu 24.04 cloud-init QCOW2 image，須包含 cloud-init、systemd、apt。程式**不會自行下載未經核實的磁碟映像**。
- 測試 VM 需能透過 Host 出站上網安裝 Docker、Compose v2、NGINX、libvirt 等 apt 套件。
- `same-name-vms` 需要第一層 Host A、B 內均有 `/dev/kvm`，否則明確判為失敗，不冒充 Nested VM 測試成功。
- 預設使用固定網段；與現有路由重疊時預檢會拒絕部署。**請勿在同一台實機同時建立多套固定網段的 Lab**。

## 指令

```bash
# 檢視拓樸，不需要 root 或 KVM
./tests/e2e/lab.sh plan

# 一鍵建立五台 VM、三個網路，並安裝中央與 Host 監控元件
sudo ./tests/e2e/lab.sh up --image /path/to/debian13-cloud.qcow2

# 分階段建立，方便偵錯
sudo ./tests/e2e/lab.sh up --image /path/to/debian13-cloud.qcow2 --no-bootstrap
sudo ./tests/e2e/lab.sh bootstrap

sudo ./tests/e2e/lab.sh status
sudo ./tests/e2e/lab.sh test                  # 快速測試：連線、指標、認證
sudo ./tests/e2e/lab.sh test all              # 全部情境，含約 10 分鐘以上失聯測試
sudo ./tests/e2e/lab.sh test same-name-vms
sudo ./tests/e2e/lab.sh test disconnect-recover

# 僅在 Router VM 裡模擬故障
sudo ./tests/e2e/lab.sh fault host-a disconnect
sudo ./tests/e2e/lab.sh heal host-a
sudo ./tests/e2e/lab.sh fault host-b netem --latency 150 --loss 3 --rate 1mbit
sudo ./tests/e2e/lab.sh heal host-b

# 清理 Lab 虛擬機與網路；保留測試報告及私有狀態
sudo ./tests/e2e/lab.sh down
# 明確清除測試報告、金鑰與狀態
sudo ./tests/e2e/lab.sh down --purge
```

所有 VM 與網路名稱以 `kvm-e2e-cm-` 開頭，狀態預設保存在 `/var/lib/kvm-vm-e2e-lab/cm/`，內含測試 CA 私鑰、Host 密碼、SSH 金鑰，權限為 root only；**不可提交到 Git 或隨便上傳**。可用 `KVM_VM_E2E_STATE_ROOT` 指定其他位置。VM 磁碟是基於提供的 cloud image 建立 CoW overlay，`down` 不會刪除原始 image。

## 測試情境

| 情境 | 驗證事項 |
|---|---|
| `connectivity` | 五台 VM SSH、HTTPS TLS、中央不可直接連入私有 LAN |
| `double-nat` | 站點 NAT + libvirt WAN NAT、正確預設路由 |
| `metrics` | 中央收到兩個 Host UUID 的 VM 與 node_exporter 指標 |
| `same-name-vms` | 兩台 Nested KVM Host 均有 `e2e-web01`，中央正確區分 `host_id` |
| `latency-loss` | 150ms、1% loss、1 Mbit/s 下仍有最新指標，完成後恢復 Router 網路 |
| `disconnect-recover` | Remote Write 斷線、vmagent 佇列累積、重啟、恢復後驗證斷線期間的歷史樣本補傳 |
| `auth` | 無密碼、錯誤密碼寫入都收到 HTTP 401 |
| `host-offline` | Inventory 失聯告警轉為 firing，網路恢復後解決 |
| `mode-switch` | Hybrid → Agent → Hybrid，`host_id` 不變、恢復後仍能上報 |

每次執行 `test` 都會在 Lab 狀態目錄的 `reports/` 建立帶日期的 JSON 報告，記錄 PASS／FAIL 與錯誤原因，任一情境失敗即回傳非零狀態。中斷測試時，如 fault 仍存在，請手動執行 `heal`。

## 邊界與已知限制

1. 目前開發執行環境沒有可供測試的實體 KVM／Docker daemon，**離線測試通過不代表真實五台 VM 已成功建立**。需要實機執行並保留 JSON 報告。
2. 首次自動部署依賴 cloud-init 的網卡命名、各 Linux 發行版的 Docker Compose v2 套件名稱與網際網路套件來源。若不相容，應修正並加入測試。
3. 本機多 NAT 可驗證傳輸與故障邏輯，但不能取代真正跨 ISP／跨機房的獨立故障驗證。
4. 測試專用 CA 期限七天，不使用 `insecure_skip_verify`；正式憑證與認證輪替為獨立待辦。
5. 本版自動化尚未涵蓋 Alertmanager Email／Webhook **實際送達**、完整備份還原、實際 Guest OS 的負載與 Agent Queue 滿載測試，請勿視為已完成。
6. `e2e-web01` 是無 OS 開機碟的真實 QEMU Domain，可驗證 libvirt VM 身分與指標，但不能代表實際 OS 的 CPU／記憶體負載測試。

## 不需要 KVM 的離線檢查

```bash
python3 -m pytest tests/test_monitoring_e2e_lab.py -q
bash -n tests/e2e/lab.sh
python3 -m py_compile tests/e2e/lab.py tests/e2e/inject_lab_ca.py
```

獨立的安裝中斷、兩租戶 queue 移交與空白 VM 還原演練，可在 bootstrap 後逐一執行。詳見[演練步驟與驗收條件](../../docs/plans/20261118_central-hardening/README.md)。
