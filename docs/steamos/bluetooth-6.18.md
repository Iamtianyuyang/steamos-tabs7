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

### 冷启动自动上电（已复验）

干净重启（uptime 0 分钟、没有手工 `rfkill` 或 `hciconfig`）后，`hci0` 直接是
`UP RUNNING`，`bluetoothctl show` 也直接给出控制器与 `Powered: yes`。
所以持久化的 rfkill 状态改成 `0` 之后，开机即用。证据
`out/wifi-6.18/bootD-bt-autopower.txt`。

## 配对可以用，但连接失败（A2DP 不在）

真机上蓝牙适配器、扫描、配对都正常，但 `bluetoothctl connect` 到耳机/手机都返回
`org.bluez.Error.Failed br-connection-profile-unavailable`，bluetoothd 日志：

```text
src/service.c:btd_service_connect() a2dp-sink profile connect failed for 88:92:CC:E5:1D:AE: Protocol not available
```

### 这一轮已经修掉的三个配置缺口

| 缺口 | 现象 | 修法 |
| --- | --- | --- |
| `CONFIG_BT_BREDR` / `BT_LE` 未开 | 配对能成，但任何 profile 连接都是 `Protocol not available`；`sco.o` 受 `BT_BREDR` 控制 | 片段补 `BT_BREDR`、`BT_LE`、`BT_RFCOMM`、`BT_BNEP` |
| `CONFIG_HID_SUPPORT` 未开 | `HID`/`UHID`/`BT_HIDP` 全部被静默丢弃（`menuconfig` 门禁） | 片段补 `HID_SUPPORT`、`HID`、`UHID`、`BT_HIDP` |
| 缺 AF_ALG | `generate_and_write_irk() Failed to open crypto`、`No IRK stored` | 片段补 `CRYPTO_USER_API{,_HASH,_SKCIPHER}` 等 |

第三项在真机上确认有效：重启后日志从 `Failed to open crypto` 变成
**`Generated IRK successfully`**（且不再有 `No IRK stored` 之后的报错）。

### 仍然失败的具体位置

清空 `dmesg` 并用 `bluetoothd -d -n` 抓到：

```text
profiles/audio/a2dp.c:a2dp_sink_probe() path /org/bluez/hci0/dev_88_92_CC_E5_1D_AE
profiles/audio/a2dp.c:a2dp_sink_connect() path /org/bluez/hci0/dev_88_92_CC_E5_1D_AE
src/service.c:btd_service_connect() a2dp-sink profile connect failed ...: Protocol not available
```

也就是失败发生在 `a2dp_sink_connect()` 的最开头，`avdtp_*` 一条日志都没打出来，
说明还没走到 AVDTP/L2CAP 建链。内核侧已排除项（都在真机上验证过）：

- `CONFIG_BT_BREDR=y`、`CONFIG_BT_LE=y`（`/proc/config.gz` 确认运行内核里是开的）；
- 用 python 建 socket：`BTPROTO_L2CAP` OK、`BTPROTO_SCO` OK（`sco.o` 存在）；
- `hciconfig hci0` 为 `UP RUNNING PSCAN`，`ACL MTU 1024:7 SCO MTU 240:4`；
- 扫描能发现真实设备（12 秒 18 台），两台音频设备已 `Paired: yes / Bonded: yes`。

### strace 结论：失败不在内核

把 `bluetoothd` 换成带 debug 的实例并用 `strace -f -e trace=socket,setsockopt,connect`
跟踪一次连接（手机 `0C:BD:75:AA:23:AC`，仍在 bond 里）：

- trace 里**没有任何一次失败的 syscall**（`grep '= -1'` 为空）；
- 能看到的都是成功的 L2CAP 建 socket 与选项设置：
  `socket(AF_BLUETOOTH, SOCK_SEQPACKET, BTPROTO_L2CAP)`、
  `setsockopt(SOL_BLUETOOTH, BT_SECURITY, " ")`、
  `setsockopt(SOL_BLUETOOTH, BT_DEFER_SETUP, [30])` 全部返回 0；
- trace 停在最后一次 `setsockopt(23, BT_DEFER_SETUP)`，**没有出现 `connect()`**。

也就是说 `Protocol not available` 是 **bluetoothd 自己返回的**（A2DP profile
在该构建里拿不到可用的本地端点/编解码器），不是内核对 socket 的拒绝。内核侧的
结论因此是完整的：BR/EDR、LE、L2CAP/SCO socket、控制器固件、射频（能扫到设备、
能配对、能生成 IRK）都正常。

这也解释了为什么前面那些内核配置补齐只改变了周边行为（IRK 生成、HID/LE 可用），
却没有改变 A2DP 的错误。

### 根因：BlueZ 没有任何本地 A2DP 端点

继续往用户空间查，证据链如下（`out/wifi-6.18/`）：

| 检查 | 结果 |
| --- | --- |
| `bluetoothd -d` 日志里 Endpoint 相关行 | **0 条**；只有 `media_server_probe() path /org/bluez/hci0`（Media1 服务端已就绪，等人注册端点） |
| strace 中的失败 syscall | 无（`grep '= -1'` 为空），socket/选项全部成功，trace 停在 `connect()` 之前 |
| SPA 蓝牙插件 | 存在：`/usr/lib/spa-0.2/bluez5/libspa-bluez5.so` + `codec-bluez5-{aac,aptx,...}.so` |
| WirePlumber | 0.5.18、PipeWire 1.6.8 在跑（`wpctl status` 可见 gamescope/Steam 客户端、`Built-in Audio`） |
| WirePlumber 蓝牙脚本 | `/usr/share/wireplumber/scripts/monitors/bluez/` 齐全；`/etc/wireplumber/wireplumber.conf.d/20-bluetooth.conf` 只禁了 `monitor.bluez-midi` |
| WirePlumber 日志 | 只有 libcamera 插件警告，没有任何 bluez 报错 |

结论：`org.bluez.Media1.RegisterEndpoint` 从未被调用，所以 A2DP profile
没有本地 SEP 可用，`a2dp_sink_connect()` 立刻返回 `ENOPROTOOPT`——这正是我们
看到的 `Protocol not available`。**内核、设备树、固件在这条链上都已经没问题**，
缺的是 PipeWire/WirePlumber 没有把 A2DP 端点注册给 BlueZ。

### WirePlumber 根本没和 BlueZ 说话

又做了两个更细的检查：

1. `WIREPLUMBER_DEBUG=D` 重启 `wireplumber`：bluez 监视器的 Lua 脚本都加载了
   （`monitor/bluez/{name-device,create-device,enumerate-device,...}` 的 hook 全部
   `Registered hook ... successfully`），日志里**没有任何 bluez 相关错误**。
2. `busctl monitor org.bluez --match='type=method_call'` 跨一次
   `systemctl --user restart wireplumber`：**0 次 method call**。

也就是说 WirePlumber 从头到尾没有向 `org.bluez` 发起任何调用，
`org.bluez.Media1.RegisterEndpoint` 自然从未发生 → A2DP 没有本地 SEP →
`a2dp_sink_connect()` 立刻 `ENOPROTOOPT`。镜像里
`/usr/lib/spa-0.2/bluez5/libspa-bluez5.so` 与 codec 插件都在，
`/etc/wireplumber/wireplumber.conf.d/20-bluetooth.conf` 只关了 `bluez-midi`，
`wireplumber.conf` 里也没有关闭 `monitor.bluez`（只有 `mixin.systemwide-session`
里关掉 `monitor.bluez.seat-monitoring`）。

### 更正一个中间结论：文件没缺，是监视器不启动

一度以为 `monitors/bluez.lua` 被镜像删了，实际不是：0.5.18 已经改成事件驱动，
`provides = monitor.bluez` 由 `scripts/monitors/bluez/enumerate-device.lua` 提供，
`pacman -Qkk wireplumber` 报告 **328 个文件、0 个被改动**，包是完整的。
`wireplumber.conf` 里 `monitor.bluez` 及其全部 hook 都在，`WIREPLUMBER_DEBUG=D`
显示它们全部注册成功。

真正的现象是：`PIPEWIRE_DEBUG=4` 下整份日志里只有一条 bluez 记录，就是 SPA 库
映射 `api.bluez5.* → bluez5/libspa-bluez5`，**这个工厂从未被实例化**，所以
PipeWire 从来没有去连 BlueZ 的 D-Bus（与 `busctl monitor` 的 0 次调用一致）。

| 层 | 结论 |
| --- | --- |
| 内核 / 固件 / 控制器 | 已验证可用（见上） |
| BlueZ（适配器、扫描、配对、IRK） | 已验证可用 |
| WirePlumber 配置与 bluez hook | 已加载，但 `api.bluez5.monitor` 设备从未被创建 |
| 结果 | BlueZ 没有 A2DP 端点 → `Protocol not available` |

### 下一步（SteamOS 用户空间）

1. 在 PipeWire 运行中**触发一次 bluez 监视器事件**并抓 `create-bluez-device`
   相关日志（`WIREPLUMBER_DEBUG=D` + `systemctl --user restart wireplumber`），
   确认 `enumerate-device.lua` 是否真的枚举到适配器；如果它压根没跑，问题在
   profile/component 解析，而不是 BlueZ。
2. 对照 vendor 4.19 镜像：那边 A2DP 若可用，说明这是该 SteamOS 镜像的
   PipeWire/WirePlumber 组合问题，与主线内核无关；若也不可用，则镜像本身不提供
   经典蓝牙音频支持，需要换/补 PipeWire、WirePlumber 或 BlueZ。
3. 这条链路已经与设备树、内核配置无关，属于 SteamOS 适配层。

### 仍未做

- 真实出厂 BD_ADDR（需要 vendor 4.19 内核读 `/efs/bluetooth/bt_addr`）；
  当前用的是本地管理地址 `02:00:00:00:5A:AD`。
- A2DP/HFP 连通：卡在 `api.bluez5.monitor` 从未实例化，见上。

