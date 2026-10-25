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

## 未验收：BlueZ 起不来

`hciconfig` 显示 `DOWN RAW`，BlueZ 侧 `bluetoothctl show` 报
`No default controller available`。`hci0` 自己的 rfkill 读出来是
`state 1 / soft 1`，向 `soft` 写 0 后仍然读回 1——和 Wi-Fi 那个 phy0 的表现一样
（`state 0` 与 `soft 1` 读数不一致）。这一步与 WiFi 的 rfkill 异常是同一个待查
问题，而不是蓝牙固件或 UART 的问题。

证据：`out/wifi-6.18/bootA-bt.txt`、`bootA-bt-userspace.txt`、
`bootA-bt-power2.txt`、`boot9-bt.txt`（首轮 `qca/qca/` 路径错误）、
`boot7-serdev*.txt`（serdev 逐层排查）。
