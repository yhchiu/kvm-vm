[English](access.md) | 繁體中文

# 存取與安全

- [預設政策](#預設政策)
- [使用 SSH 連線](#使用-ssh-連線)
- [Console 密碼登入](#console-密碼登入)
- [以新 SSH 金鑰重新取得存取權](#以新-ssh-金鑰重新取得存取權)

## 預設政策

每台 VM 一開始都套用以下 cloud-init 設定：

- 管理帳號（`cloud_init.user`，預設 `admin`）的**密碼鎖定**（`lock_passwd: true`），並具免密碼 `sudo` 權限。
- SSH **僅接受金鑰**（`ssh_pwauth: false`）。
- root 無法透過 SSH 登入（`disable_root: true`）。此設定只管 SSH，不決定 root 帳號的密碼政策；自訂映像檔會保留其原有的 root 帳號設定。

不會設定 root 密碼，也不會開啟主控台自動登入。若需要規劃主控台存取，請使用 [Console 密碼登入](#console-密碼登入)，不要把密碼放進 YAML 或 `runcmd`。

## 使用 SSH 連線

查出位址後，以管理帳號與對應的私鑰連線：

```bash
sudo kvm-vm list --ips
ssh admin@<vm-ip> -i ~/.ssh/id_ed25519
ssh -p 2222 admin@<vm-ip> -i ~/.ssh/id_ed25519   # cloud_init.ssh_port 為 2222 時
```

Macvtap VM 無法從自己的宿主機連線，請從其他機器連線或使用主控台。詳見 [Macvtap 限制](networking.zh-TW.md#macvtap-限制)。

## Console 密碼登入

`kvm-vm console` 會連上序列主控台，但因為密碼鎖定，預設無法在那裡登入。Console 密碼登入會為管理帳號產生隨機密碼。它適用所有網路模式，也是從宿主機管理 Macvtap VM 的方式。SSH 仍維持僅金鑰登入。

宿主機需要 OpenSSL；憑證檔以 POSIX 擁有權與權限寫入，因此宿主機必須是 Linux。

### 開啟方式

將以下欄位合併到定義檔的 `cloud_init` 區段。SSH 金鑰仍為必填，請保留。

```yaml
cloud_init:
  user: admin
  console_password: true
  ssh_authorized_keys:
    - file:~/.ssh/id_ed25519.pub
```

建立 VM 時，在私有目錄中指定一個新的憑證檔：

```bash
sudo install -d -m 0700 /root/vm-credentials
sudo kvm-vm create web01.yaml --console-password-file /root/vm-credentials/web01.json
sudo kvm-vm console web01
```

在登入提示中，輸入檔案裡的 `user` 與 `password`。

### 憑證檔

- 路徑必須是**新**檔案，所在目錄須屬於執行 kvm-vm 的管理員，且其他人無法存取。既有檔案與符號連結會被拒絕。
- 檔案權限為 0600，內容為 JSON，含 `vm`、`user`、`password`。密碼為 24 個隨機字元。
- 密碼不會顯示在畫面上，也不會存入 kvm-vm 的定義檔或狀態。只有加鹽雜湊會寫入權限受限的 cloud-init 檔案。
- 定義檔設定 `console_password: true` 時，`create`、`clone`、`reinstall` 都必須指定 `--console-password-file`，包括 `--dry-run`。Dry run 只檢查路徑，不建立檔案。`validate` 不會產生密碼。設定未開啟時指定此選項會報錯。

### Guest 內的處理

密碼會一直鎖定到最後一個首次開機步驟。該步驟會關閉 SSH 的密碼與 keyboard-interactive 登入、檢查 SSH 設定、啟動序列主控台登入服務，之後才解鎖密碼。SSH 金鑰登入維持可用。

`create` 成功結束不代表此步驟成功。請檢查 cloud-init，並實際試著從主控台登入。

### 風險與責任

- 密碼不限於序列主控台使用。持有密碼的人可透過管理帳號的免密碼 `sudo` 取得管理員權限。
- 密碼不會過期，也不會要求使用者更換。
- 請保護好檔案。即使 `create` 在後續步驟失敗也要保留，因為 VM 的檔案可能已經使用這組密碼。重試時需要新的檔案路徑。
- `kvm-vm delete` 不會刪除此檔案，請自行清理。
- 若有人在 guest 內修改密碼，檔案內容就會過時。
- 沒有找回密碼的指令。遺失時請使用其他存取方式，或下方的離線方法。

### Clone 與 reinstall

`clone` 依**目標**定義檔的設定。設定開啟時，請指定新檔案：

```bash
sudo kvm-vm clone web01 web02.yaml --console-password-file /root/vm-credentials/web02.json
```

每台 clone 都有自己的密碼。新 VM 首次開機前，kvm-vm 會在複製出的磁碟上鎖定管理帳號的密碼，包括來源 VM 原本的密碼。只有設定開啟的 clone 會以新密碼解鎖。來源 VM 不會被修改。若寫入憑證失敗，不會動到任何 guest 檔案；若之後的步驟失敗，clone 的檔案與憑證都會保留。

`reinstall` 沿用已儲存的設定。設定開啟時，請指定新檔案；VM 會取得新密碼，舊密碼失效：

```bash
sudo kvm-vm reinstall web01 --yes --console-password-file /root/vm-credentials/web01-reinstalled.json
```

新憑證會在 VM 關機或替換磁碟之前寫入。既有的憑證檔絕不會被覆寫或刪除。設定未開啟、或在此功能推出前建立的 VM，會維持密碼鎖定，不需指定檔案。`--dry-run` 不會產生憑證。

### 已測試的 guest

Debian 12 與 13、Ubuntu 24.04 與 26.04、Rocky Linux 8、AlmaLinux 8 已通過主控台登入、`sudo`、SSH 僅金鑰登入與重開機檢查，也驗證了三種網路模式下的預設鎖定、clone 憑證隔離與 reinstall 密碼更換。Rocky Linux 與 AlmaLinux 9、10 尚未測試主控台登入。詳見 [Console 驗收報告](plans/20261019_macvtap-console/ticket-06-validation.md)（英文）。

## 以新 SSH 金鑰重新取得存取權

若遺失私鑰且沒有 Console 密碼，可在 VM 關機時把新公鑰加入磁碟：

1. 將 VM 關機並等待完全關閉：

   ```bash
   sudo virsh shutdown web01
   sudo virsh domstate web01      # 等到顯示 "shut off"
   ```

2. 用 `virt-customize` 加入新金鑰：

   ```bash
   sudo virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
   ```

3. 啟動 VM，並以新金鑰連線：

   ```bash
   sudo virsh start web01
   ssh admin@<vm-ip> -i /path/to/new_key
   ```

切勿修改執行中 VM 的磁碟。
