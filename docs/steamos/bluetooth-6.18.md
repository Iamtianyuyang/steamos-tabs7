# 主线 6.18 上的蓝牙：QCA6390 BT 半（第一个阶段）

蓝牙在 QCA6390 封装里，和 Wi-Fi 共用 PMU，但走独立的上行：QUP0 SE6 UART，
由 `hci_qca` 以 serdev 方式驱动。厂商 4.19 那套（`sec_bluetooth` + vendor
`bluetooth` 节点）在这里不适用。

## 证据：这条 UART 和这个使能脚

| 事实 | 来源 |
| --- | --- |
| BT 走 `qupv3_se6_4uart`（`0x998000`，主线 `&uart6`） | 厂商 `bluetooth_gts7l_common.dtsi` 末尾启用的就是它；`kona-qupv3.dtsi` 把这个标签定义在 `0x998000` |
| BT_EN = TLMM 76 | 同一文件：`qca,bt-reset-gpio = <&tlmm 76 0>` 且 `bt_en_sleep` 状态把 `gpio76` 拉低 |
| `&qupv3_se3_4uart`（`0x98c000`）不是 BT | 它定义在三星的 `gps_bcm47531.dtsi` 里，是 GPS 的 UART |
| 实机 vendor DT 的 `bt-en-gpio = <21>` 不可信 | 21 在本板 `gpio-reserved-ranges = <20 4>`（TrustZone 指纹 SPI）里，板级 dtsi 与 pinctrl 都写 76 |
| qca6390 不自己拉 vreg | `hci_qca.c` 的 `qca_soc_data_qca6390` 是 `num_vregs = 0` |

## 实现

- `&uart6` 打开并挂一个 `bluetooth` serdev 子节点：
  `compatible = "qcom,qca6390-bt"`，`firmware-name = "htnv20.bin", "htbtfw20.tlv"`。
- **`enable-gpios` 不放在 serdev 节点上**：`hci_qca` 对 qca6390 在 devicetree
  场景下强制走 pwrseq（`devm_pwrseq_get(..., "bluetooth")`），使能线由 PMU 节点
  的 `bt-enable-gpios = <&tlmm 76 GPIO_ACTIVE_HIGH>` 驱动。serdev 节点只保留
  PMU LDO 的 supply 句柄，因为 `pwrseq_qcom_wcn_match()` 靠
  `vddaon-supply → LDO → regulators → PMU` 认领消费者。
- `aliases { serial0 = &uart6; }`：`qcom_geni_serial` 从 `serial` alias 取 line
  编号，没有 alias 时 probe 直接以 `Invalid line -19` 失败，且不产生 hci 设备。
  这不是 console alias（命令行仍是 `console=tty0`）。
- 新片段 `steamos-bluetooth-6.18.config`：`SERIAL_DEV_BUS`、`SERIAL_DEV_CTRL_TTYPORT`、
  `BT`、`BT_HCIUART`、`BT_HCIUART_SERDEV`、`BT_HCIUART_QCA`、`BT_HCIVHCI`。
  其中 **`SERIAL_DEV_CTRL_TTYPORT` 是关键**：它 `default y` 但也带 prompt，
  `allnoconfig` 会关掉，此时 serdev 只编译 core，`serial_core` 退化成注册普通
  tty——`ttyHS0` 出现、`hci0` 不出现、没有任何错误日志。
- initramfs 里放 `lib/firmware/qca/htnv20.bin` 与 `htbtfw20.tlv`（上游
  linux-firmware，SHA-256 与来源在 ignored `out/firmware/bluetooth/manifest.json`）。
  注意 DT 里写**裸文件名**：驱动自己会加 `qca/` 前缀，写成 `qca/htnv20.bin`
  会变成 `qca/qca/...` 并加载失败。
- shim reserve 从 24 MiB 提到 28 MiB：BT/HCI 栈让 `image_size` 超过 24 MiB 能容纳
  的上限（`0x12f0000` + 4 MiB 迁移 + DTB + 栈）。

## 真机结果（内核侧已通）

候选 BOOT `out/steamos-kernel-6.18/wifi-bt5/boot.img`（SHA-256
`1db55fdf3a67274c540e2d081f934e3814376ca1eefb44bc6ab6adcdb51f5479`）。

| 观察 | 证据 |
| --- | --- |
| serdev 客户端绑定 | `serial serial0: tty port ttyHS0 registered`，`/sys/bus/serial/devices/serial0-0` |
| 固件下载 | `QCA Downloading qca/htbtfw20.tlv` → `qca/htnv20.bin` → **`QCA setup on UART is completed`** |
| 控制器识别 | `QCA Product ID 0x10 / SOC 0x400a0200 / ROM 0x00000200 / Patch 0x00000d2b` |
| hci 设备 | `/sys/class/bluetooth/hci0` 存在；`hciconfig`：`Type: Primary  Bus: UART`，`BD Address 00:00:00:00:5A:AD`，RX/TX 计数非零 |

## hci0 的 unconfigured：根因与修复（已解决）

现象：`hciconfig` 显示 `DOWN RAW`，`bluetoothctl show` 报
`No default controller available`，`hciconfig hci0 up` 返回
`Can't init device hci0: Operation not supported (95)`。

### 两层原因

1. **rfkill 的持久化状态**：`/var/lib/systemd/rfkill/` 里
   `platform-998000.serial:bluetooth` 和 vendor 时代的
   `platform-vendor:bt_qca6390:bluetooth` 都存着 `'1'`，`systemd-rfkill`
   开机套用到新设备上，于是 `rfkill list` 把 `hci0` 标成软阻塞。
   `rfkill unblock bluetooth` 解决，并把状态改写成 `'0'`（现在该文件已是 `0`；
   旧的 `platform-vendor:...` 文件仍是 `1`，但那个设备名不会再出现）。
   sysfs 的 `state` 仍读 1，这是已知的 sysfs 读数不一致，以 netlink 为准。
2. **`HCI_UNCONFIGURED`**：`hci_dev_open()` 对 unconfigured 设备返回
   `-EOPNOTSUPP`。该标志由 `hci_dev_setup_sync()` 在 BD_ADDR 无效时设置。
   控制器报的是 `00:00:00:00:5A:AD`（ROME 默认值），因为上游
   `htnv20.bin` 不带这台设备的出厂地址；而 `hci_qca` 从不设置
   `HCI_QUIRK_USE_BDADDR_PROPERTY`，所以 DT 里写了 `local-bd-address` 也无效。

### 出厂地址读不出来

三星把 BT MAC 放在 **加密的** `efs` / `sec_efs`（首字节即随机密文，mainline
既不能挂载也不能解密），`bluetooth`/`btd` 分区全 0，`param`/`apdp` 里没有可识别
的地址，SteamOS 的 `set_mac_addresses.sh` 读的是 Steam Frame 的 EEPROM
（`eeprom_console get mac_addr_ble`），在这台机器上返回空。所以 mainline 无法
取得原始地址。

### 处理方式

- `drivers/bluetooth/hci_qca.c`：在拿到 `hdev` 之后，如果 DT 有
  `local-bd-address` 且没有 `qcom,local-bd-address-broken`，就设置
  `HCI_QUIRK_USE_BDADDR_PROPERTY`（照 `btnxpuart` 的做法）。这样
  `hci_dev_setup_sync()` 会用 DT 地址走 `set_bdaddr`，不再标 unconfigured。
- DT：`local-bd-address = [ ad 5a 00 00 00 02 ]`，即
  **02:00:00:00:5A:AD**（本地管理位、单播）。注意该属性是**小端**字节序：
  最初写成 `[ 02 00 00 00 5a ad ]`，控制器报出来就是 `AD:5A:00:00:00:02`，
  这一点是在真机上确认的。
- 这是**本地管理地址**，不是三星出厂地址，而且对每个 gts7lwifi 构建都相同；
  真实地址需要回到 vendor 4.19 内核里读 `/efs`，属于后续可选工作。

### 真机结果

候选 BOOT `out/steamos-kernel-6.18/wifi-bt7/boot.img`
（SHA-256 `a167a51f0e4660f17aad283e39c30de1d6a64be2c80502aa3ea7e9e1a2149801`）。

| 观察 | 证据 |
| --- | --- |
| 控制器上电 | `hciconfig hci0`：`BD Address 02:00:00:00:5A:AD`，`ACL MTU 1024:7 SCO MTU 240:4`，**`UP RUNNING`** |
| BlueZ 接管 | `bluetoothctl show`：`Controller 02:00:00:00:5A:AD (public)`，`Manufacturer 0x001d (Qualcomm)`，`Powered: yes`，`Name: frame` |
| 扫描 | `bluetoothctl scan on` 12 秒发现 **18 台设备**（含 `midea`、`54:14:A7:…` 等），`devices` 列表可读 |
| rfkill 状态 | `/var/lib/systemd/rfkill/platform-998000.serial:bluetooth` 现为 `0` |

证据：`out/wifi-6.18/bootB-bt.txt`、`bootB-bt-scan.txt`（首轮扫描，地址尚未修正）、
`bootC-bt.txt`、`bootC-bt-final.txt`（修正字节序后）、`bt-efs.txt`、
`bt-partition.txt`、`bt-param-apdp.txt`（出厂地址不可得）、
`bootA-rfkill-state.txt`（持久化 rfkill 状态）。

### 仍未做

- 真实出厂 BD_ADDR（需要 vendor 4.19 内核读 `/efs/bluetooth/bt_addr`）。
- 冷启动后是否需要人工干预：本轮状态文件已改为 `0`，但"重启后自动上电"还没有
  单独复验。
- 配对/连接（需要一台可控的外设）。
