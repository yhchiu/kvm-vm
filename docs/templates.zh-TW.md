[English](templates.md) | 繁體中文

# VM 定義檔範本

範本是一般 VM YAML 定義檔，不是額外的 Template Engine。安裝後即可使用常見 VM 規格，不必保留 Git checkout。建立前請確認生效設定。

## 查詢及使用

```sh
kvm-vm template list
kvm-vm template show debian13-bridge
sudo kvm-vm validate @ubuntu24-nat
sudo kvm-vm create --template ubuntu24-nat --dry-run
sudo kvm-vm create @ubuntu24-nat
sudo kvm-vm validate @clone-target-nat --clone
sudo kvm-vm clone nat01 @clone-target-nat --dry-run
```

`validate`、`create`、`clone` 均支援 `@NAME` 和 `--template NAME`，兩者等價，`.yaml` 副檔名可省略。只能選擇一種輸入來源。原有一般檔案路徑（`./my-vm.yaml`、`/srv/vm.yaml`、`~/vm.yaml`，包括 `./@literal.yaml`）維持原本行為；不存在的一般檔案不會 fallback 到範本。`~NAME` 不是範本語法。

`template list` 每個生效名稱只列出一次，顯示來源（`local` 或 `builtin`）與參考類型（`create` 有映像檔來源，`clone` 無映像檔來源，`invalid` 無法解析）。這不是完整驗證，仍需使用 `validate`（clone 請加 `--clone`）。`template show` 顯示原始 YAML。兩個指令都不需要 libvirt 正在執行。查找來源及路徑會顯示於 stderr，不影響 YAML 輸出。`create` 仍會執行驗證、宿主機預檢及確認提示，`--yes` 只略過確認。

## 目錄與升級

| 目錄 | 用途 |
|---|---|
| `/etc/kvm-vm/templates/` | 管理員自訂範本，優先查找，安裝程式不覆寫 |
| `/usr/local/share/kvm-vm/templates/` | 內建範本，由 `examples/*.yaml` 安裝及更新 |
| `/etc/kvm-vm/definitions/` | 已管理 VM 的生效定義檔，不是範本 |

`install.sh` 建立所需目錄，內建 YAML 權限為 0644。再次執行會更新內建範本，保留自訂範本及既有 VM 定義檔，不自動刪除額外的舊內建檔案。建議複製到自訂範本目錄後再編輯，例如（預設安裝路徑）：

```sh
sudo install -m 0644 /usr/local/share/kvm-vm/templates/ubuntu24-nat.yaml \
  /etc/kvm-vm/templates/hosting-standard.yaml
sudoedit /etc/kvm-vm/templates/hosting-standard.yaml
sudo kvm-vm validate @hosting-standard
```

同名的自訂範本會完整覆蓋內建範本的查找結果，不會合併 YAML。若自訂範本有問題則直接報錯，不會自動使用內建版本。範本名稱限單一檔名，開頭為英數字元，可包含英數字元、`_`、`-`、`.`。禁止路徑穿越及 symbolic link。Root 操作時，範本檔案、絕對路徑目錄及其上層目錄必須由 root 擁有，且 group/others 不可寫入（例如 `/tmp` 這種 root 擁有的 sticky 上層目錄可放置安全的子目錄）。範本可以包含 Guest 指令、映像檔路徑及 SSH 金鑰，應視為受信任管理員的輸入。

安裝程式依 `PREFIX`（預設 `/usr/local`）決定內建範本路徑。安裝於 `PREFIX/sbin` 或 `PREFIX/bin` 時，執行時可自動取得 prefix。若 `DEST` 為其他任意位置，安裝及使用時請明確設定 `KVM_VM_BUILTIN_TEMPLATE_DIR`。可分別使用 `KVM_VM_TEMPLATE_DIR` 與 `KVM_VM_BUILTIN_TEMPLATE_DIR` 覆寫兩個範本目錄（絕對路徑），`KVM_VM_DEF_DIR` 仍獨立，三者不可指向同一目錄。未安裝而直接從 repo 執行時，可用一般 YAML 路徑或明確設定內建目錄。

## 常用範本

| 發行版 | NAT（DHCP） | Bridge（DHCP） | Macvtap（DHCP） |
|---|---|---|---|
| Ubuntu 24.04 | `ubuntu24-nat` | `ubuntu24-bridge` | `ubuntu24-macvtap` |
| Ubuntu 26.04 | `ubuntu26-nat` | `ubuntu26-bridge` | — |
| Debian 13 | `debian13-nat` | `debian13-bridge` | `debian13-macvtap` |
| Debian 12 | `debian12-nat` | — | — |
| Rocky Linux 9 | `rocky9-nat` | `rocky9-bridge` | — |
| Rocky Linux 10 | `rocky10-nat` | — | — |
| AlmaLinux 9 | `almalinux9-nat` | `almalinux9-bridge` | — |
| AlmaLinux 10 | `almalinux10-nat` | — | — |

為維持相容性，保留 `ubuntu24-web01`（Bridge／Static IP）、`debian13-dhcp`（Bridge／DHCP）及 `clone-target`（Bridge／Static IP）。`clone-target-nat` 則提供 DHCP 的 clone 目標。Clone 範本不含基礎映像檔，應使用 `validate --clone` 或 `clone`，不是 `create`。

新增 create 範本採用 2 vCPU、4 GiB RAM、40 GiB Disk、q35、自動 MAC、SSH key-only 的 `admin`、QGA 及 balanced 維護政策。它們不是自動偵測宿主機的設定。NAT 需要已啟用的 `default` libvirt 網路，Bridge 需要已存在的 `br0`（或自訂 bridge），Macvtap 需要實際存在的 Ethernet 介面（`eno1` 只是範例），一般無法直接與 Host 通訊。詳見[網路設定](networking.zh-TW.md)。Static IP 範本應先依 LAN 實際設定調整，Host 預檢不能保證 IP 無衝突。

可使用現有的[環境變數覆寫](configuration.zh-TW.md)功能調整名稱、IP、資源等設定，而不必修改範本：

```sh
sudo KVM_VM_NAME=web03 KVM_VM_DISK=80 kvm-vm create @debian13-nat
sudo KVM_VM_NAME=web04 KVM_VM_MACVTAP=enp2s0 kvm-vm create @ubuntu24-macvtap --dry-run
```

固定 VM 名稱不會自動遞增，重複建立時請指定不重複的名稱及網路設定。本階段不引入範本變數、繼承或額外 CLI 欄位覆寫參數。其他自訂設定請編輯 YAML。

使用具名範本時，相對 `file:` SSH 金鑰及本機映像檔路徑以該範本所在目錄為基準；原本的 `~` SSH 金鑰路徑處理維持不變。生效定義檔保存已解析的 SSH 公鑰與絕對映像檔路徑，不因範本變更或移除而改變。Reinstall 沿用保存的生效設定，但外部映像檔仍須可用。State 的 `definition_source` 記錄實際選用的範本路徑。建立 VM 不會修改範本。

監控範例已獨立移至 [`examples/monitor/`](../examples/monitor/)，不會安裝為 VM 範本。
