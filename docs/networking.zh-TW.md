[English](networking.md) | 繁體中文

# 網路設定

每台 VM 有一張網卡，可用三種模式之一連接。同一台宿主機上的不同 VM 可以使用不同模式。

- [選擇模式](#選擇模式)
- [橋接模式](#橋接模式)
- [NAT 模式](#nat-模式)
- [Macvtap 模式](#macvtap-模式)
- [IP 位址](#ip-位址)
- [查詢 VM 位址](#查詢-vm-位址)

## 選擇模式

| | 橋接（Bridge） | NAT | Macvtap |
|---|---|---|---|
| LAN 上其他機器能連到 VM | 是 | 否（僅能對外連線） | 是 |
| KVM 宿主機能透過網路連到 VM | 是 | 是 | **否** |
| 宿主機需事先準備 | Linux bridge，例如 `br0` | libvirt NAT 網路，通常是 `default` | 一張未使用的實體 Ethernet 介面 |
| 適合 | LAN 上的伺服器 | 實驗、測試、第一次試用 | 宿主機網卡無法改成 bridge 時的 LAN 伺服器 |

kvm-vm 不會建立、修改或刪除宿主機的 bridge、libvirt 網路或實體介面，請先用發行版的工具設定好。

## 橋接模式

橋接模式是預設值。省略 `network.mode` 與 `network.bridge` 時，VM 會接到 `br0`。

```yaml
network:
  mode: bridge
  bridge: br0
  ipv4:
    method: dhcp
```

等同於 `virt-install --network bridge=br0,model=virtio,mac=...`。

`KVM_VM_BRIDGE=名稱` 可在執行時選用橋接模式與指定 bridge。只設定 `network.bridge` 而沒有 `mode` 的定義檔，也會使用橋接模式。

## NAT 模式

NAT 模式把 VM 接到 libvirt 虛擬網路。libvirt 常見的 `default` 網路使用 `virbr0` bridge，以 DHCP 分配位址，並對外做 NAT。VM 可以連上網際網路，但其他機器無法連到 VM。

```yaml
network:
  mode: nat
  libvirt_network: default
  ipv4:
    method: dhcp
```

等同於 `virt-install --network network=default,model=virtio,mac=...`。完整範例請見 [`examples/ubuntu24-nat.yaml`](../examples/ubuntu24-nat.yaml)。

執行 `create` 或 `clone` 前，kvm-vm 會檢查該網路存在、已啟動且使用 NAT 轉送。若網路存在但未啟動：

```bash
sudo virsh net-start default
sudo virsh net-autostart default
```

NAT 建議使用 DHCP，由 libvirt 分配位址。靜態位址也可以，但必須自行確保位址在 NAT 子網路內，且不與 DHCP 範圍及保留位址衝突。

## Macvtap 模式

Macvtap 透過宿主機的一張 Ethernet 介面（稱為 **parent**）把 VM 直接接上實體 LAN，不需把該介面改成 Linux bridge。

```yaml
network:
  mode: macvtap
  parent: eno1
  ipv4:
    method: dhcp
```

也可以在執行時切換既有定義檔：

```bash
sudo KVM_VM_MACVTAP=eno1 KVM_VM_IPV4=dhcp \
  kvm-vm create examples/ubuntu24-web01.yaml
```

請把 `eno1` 換成你的介面。靜態位址請使用 `ipv4.address`、`gateway`、`dns`，或 `KVM_VM_IPV4`、`KVM_VM_GATEWAY`、`KVM_VM_DNS`。`KVM_VM_MACVTAP` 也會把橋接與 NAT 定義檔切換成 Macvtap。請勿同時設定 `KVM_VM_BRIDGE`，kvm-vm 會拒絕這種組合。

### Macvtap 限制

- **宿主機無法透過網路連到 VM。** SSH、ping 與所有 IP 服務都一樣，即使在同一子網路也是。請從 LAN 上的其他機器連線。`list --ips` 查得到位址，不代表宿主機連得到。
- **從宿主機管理 VM 請用序列主控台。** 執行 `sudo kvm-vm console NAME`。若要在主控台用密碼登入，請在建立 VM 前開啟 [Console 密碼登入](access.zh-TW.md#console-密碼登入)；密碼預設鎖定。
- **每台 VM 只有一張網卡。** kvm-vm 不會另外加入 NAT 或管理用網卡。不同模式的 VM 能否互通，取決於你的路由與防火牆。

### Parent 介面需求

- Parent 必須存在、已啟用，且是獨立的 Ethernet 介面。kvm-vm 透過 `/sys/class/net` 與 `ip -j -d link` 檢查。
- 不可隸屬 Linux bridge 或 bond。若 `eno1` 已加入 `br0`，請用另一張介面做 Macvtap。
- 不支援 Wi-Fi、bond、VLAN 介面、bridge 與從屬介面。
- Macvtap 一律使用 `bridge` 轉送子模式，因此同一 parent 上的 VM 可以互通。

`validate` 只檢查定義檔；`create --dry-run` 還會檢查宿主機上的 parent。

### LAN 需求

LAN 必須允許宿主機的交換器連接埠出現額外的 MAC 位址，並提供 VM 位址（DHCP 或你指派的靜態位址）。kvm-vm 無法檢查這些。交換器或供應商對 MAC 位址的限制，可能讓本機檢查全部通過的 VM 仍無法連網。請向網路管理員確認。

### 已儲存的設定

`create` 與 `clone` 會把網路連接方式存入生效定義檔。`reinstall` 沿用該設定，並忽略 `KVM_VM_MACVTAP` 等覆寫。在另一台宿主機還原定義檔時，請確認那裡有合適的 parent。

靜態位址、LAN DHCP、guest agent 位址偵測與 VM 生命週期已通過[實機驗收](plans/20261019_macvtap-console/ticket-05-validation.md)（英文）。你的 LAN 仍須符合上述需求。

## IP 位址

| `ipv4.method` | 意義 |
|---|---|
| `dhcp`（預設） | 透過 DHCP 取得位址。 |
| `static` | 使用 `address`（含前綴長度，例如 `192.168.10.51/24`）、`gateway`（必填）與 `dns`（選填清單）。 |
| `disabled` | 不使用 IPv4。 |

IPv6 預設為 `disabled`，其他選項請見[欄位參考](configuration.zh-TW.md#network)。

## 查詢 VM 位址

```bash
sudo kvm-vm list --ips
sudo kvm-vm status web01
```

kvm-vm 會向 libvirt 查詢三種來源的位址：QEMU guest agent、libvirt 的 DHCP 租約，以及宿主機的 ARP 表。因此比單純的 `list` 慢。

- NAT VM 會出現在 libvirt 的 DHCP 租約中。
- 橋接與 Macvtap VM 通常由 libvirt 不知道的 DHCP 伺服器取得位址，這時要靠 guest agent 回報，需要 `cloud_init.qemu_guest_agent: true`（預設值）。
- Guest agent 在首次開機時安裝。請等 cloud-init 完成後再依賴 `list --ips`。
