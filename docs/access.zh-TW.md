[English](access.md) | 繁體中文

# 存取與安全

- [預設政策](#預設政策)
- [使用 SSH 連線](#使用-ssh-連線)
- [Console 密碼登入](#console-密碼登入)
- [重新取得存取權](#重新取得存取權)

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
- 沒有找回密碼的指令。遺失時請使用其他存取方式，或下方的[離線方法](#重新取得存取權)。

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

## 重新取得存取權

若 VM 沒有可用的 Console 密碼，可用 `virt-customize` 離線修改它的磁碟。請依問題選擇方法：

- [設定管理帳號密碼](#設定管理帳號密碼)：VM 無法透過網路連線時使用，例如從宿主機管理的 Macvtap VM，或網路設定已損壞的 guest。設定後從主控台登入。
- [加入新的 SSH 金鑰](#加入新的-ssh-金鑰)：網路正常，但遺失私鑰時使用。

`virt-customize` 不可對執行中的 VM 使用。請先將 VM 關機並等待完全關閉：

```bash
sudo virsh shutdown web01
sudo virsh domstate web01      # 等到顯示 "shut off"
```

若 VM 有 kvm-vm [快照](snapshots.md)（英文），請使用 `-d web01`，它會修改 VM 目前使用的磁碟。不要使用 `-a .../web01.qcow2`：該檔案是快照的唯讀回復點。

### 設定管理帳號密碼

此方法為管理帳號（`cloud_init.user`，預設 `admin`）設定密碼，也就是 [Console 密碼登入](#console-密碼登入)使用的同一個帳號；不會設定 root 密碼。帳號名稱可在 VM 的生效定義檔 `/etc/kvm-vm/definitions/web01.yaml` 中查到。

**從標準輸入讀取**，適用 libvirt 中已定義的 VM。輸入時不會顯示密碼，也不會留在 shell 歷史紀錄或行程清單中：

```bash
read -rsp 'New password for admin: ' pw && echo
printf '%s\n' "$pw" | sudo virt-customize -d web01 --password admin:file:/dev/stdin
unset pw
```

**從檔案讀取並指定磁碟路徑**，適用沒有 libvirt domain 的磁碟，例如以 `delete --keep-storage` 保留的磁碟，或從備份還原的磁碟。檔案的第一行即為密碼。請以 0600 權限建立，用完即刪除：

```bash
sudo virt-customize -a /var/lib/libvirt/images/vm/web01.qcow2 \
  --password admin:file:/root/web01-admin-password
sudo rm /root/web01-admin-password
```

接著啟動 VM，並在主控台以 `admin` 登入。此帳號具免密碼 `sudo` 權限：

```bash
sudo virsh start web01
sudo kvm-vm console web01
```

- kvm-vm 會關閉 SSH 密碼登入（`ssh_pwauth: false`），但只有開啟 Console 密碼登入時才會關閉 keyboard-interactive 登入，其他 VM 則取決於映像檔。請在 guest 內確認，兩個值都應為 `no`：

  ```bash
  sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication) '
  ```

  若任一個為 `yes`，請在 SSH 設定中改為 `no` 並重啟 SSH，或在用完後立即重新鎖定密碼。
- Rocky Linux、AlmaLinux 等啟用 SELinux 的 guest，目前版本的 `virt-customize` 會自動修正變更檔案的 SELinux 標籤。較舊的版本需要加上 `--selinux-relabel`；在目前版本中此選項不做任何事，加上也無妨。
- 問題排除後，請在 guest 內執行 `sudo passwd -l admin` 重新鎖定密碼，回到預設政策。
- kvm-vm 的驗收測試涵蓋的是 Console 密碼登入，不包括此流程。

### 加入新的 SSH 金鑰

```bash
sudo virt-customize -d web01 --ssh-inject admin:file:/path/to/new_key.pub
sudo virsh start web01
ssh admin@<vm-ip> -i /path/to/new_key
```
