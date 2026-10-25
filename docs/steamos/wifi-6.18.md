# 主线 6.18 上的 Wi-Fi：QCA6390 / ath11k（第一个阶段）

设备里的 Wi-Fi 是 QCA6390 封装内的 ATH11K（PCI `17cb:1101`），走 PCIe0。
厂商 4.19 那套（CNSS2 + qcacld + `cnss-daemon` + `/dev/wlan` + 私有
NetworkManager）的实测记录在[原生 Linux Wi-Fi bring-up](../hardware/wifi-bringup.md)，
那条路径在 6.18 上不适用：主线没有 CNSS/qcacld，也不需要 IPA 初始化和
`qrtr-ns`。主线顺序是 **PCIe0 → `qcom,qca6390-pmu` 电源时序 → ath11k**。

本文只记录到这里为止真正做过的事，以及明确的未验证部分。

## 阶段 1：PMU 电源、使能线和 PCI 消费端（已完成源码级改动，未上机）

改动文件：

| 文件 | 内容 |
| --- | --- |
| `linux-sm8250-v6.18/arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi` | 根节点加 `qca6390-pmu`；`&pcie0`/`&pcie0_phy` 打开；`&pcieport0` 下加 `wifi@0` |
| `hardware/samsung/gts7lwifi/kernel/configs/steamos-wifi-6.18.config` | PCIe/PMU 时序/cfg80211/mac80211/ath11k/rfkill |
| `tests/steamos/test_wifi_pmu.py` | 逐项对照 vendor 源码与实机 vendor DT 的约束 |

### 轨道映射来自本机的实机 vendor DT

vendor 源码 `kona.dtsi` 的默认值和这台的实机 DT 并不完全一样（`vdd-wlan-dig`
在 SoC dtsi 里是 `pm8009_s2`，`gts7lwifi` 把它改成 `pm8150_s6`；实机 DT 也是
`pm8150_s6`）。因此取 **实机 vendor DT + 板级覆盖** 为证据，而不是上游参考板。
Samsung 的 `pm8150a` 就是主线的 `pm8150l`，所以 `pm8150a_s8` 对应 `vreg_s8c_1p35`
（smps8，1.35 V）。

| PMU 输入 | vendor 轨道（实机 DT） | 电压 | 主线轨道 |
| --- | --- | --- | --- |
| `vddaon` | `pm8150_s6` | 950000 | `vreg_s6a_0p95` |
| `vddpmu` | `pm8150_s6`（板级覆盖；SoC dtsi 默认 `pm8009_s2`） | 950000–952000 | `vreg_s6a_0p95` |
| `vddrfa0p95` | 与 `vdd-wlan-dig` 同一路 | 950000 | `vreg_s6a_0p95` |
| `vddio` | `pm8150_s4` | 1800000 | `vreg_s4a_1p8` |
| `vddrfa1p9` / `vddpcie1p9` | `pm8150_s5`（`vdd-wlan-rfa1`） | 2040000 | `vreg_s5a_1p9` |
| `vddrfa1p3` / `vddpcie1p3` | `pm8150a_s8`（`vdd-wlan-rfa2`） | 1350000 | `vreg_s8c_1p35` |

`wlan-enable-gpios = <&tlmm 90 GPIO_ACTIVE_HIGH>`，与 vendor 的 `wlan-en-gpio`
和 `qca6390_wlan_en_active`（`pins = "gpio90"`）一致。

### 与 vendor 不同的两处，尚未在真机判定

1. **s5 电压**：vendor 把 `pm8150_s5` 固定成 2.04 V；本板 `vreg_s5a_1p9` 保持
   1.9 V 默认值（上限允许 2.04 V）。这一路同时是 `pm8150l` LDO7/12/14/15 的
   输入，为了不给别的设备改电压，这次没有动它。1p9/PCIE 1p9 落在同一路上，
   两种设置都只是这一路的电平，真机上如果 ath11k 起不来，这是第一个要复核的
   参数，不是先改 DT 猜。
2. **蓝牙使能脚**：vendor 源码写 `qcom,bt-en-gpio = <&tlmm 76 0>`，这台的实机
   vendor DT 却写 GPIO **21**，而 21 在本板 `gpio-reserved-ranges = <20 4>`
   （TrustZone 指纹 SPI）里。两个值都不能直接采信，所以这次 **`qca6390-pmu`
   不声明 `bt-enable-gpios`**，蓝牙留到独立的一步再定。

另外 vendor 还接了 `wlan-ant-switch-supply = <&pm8150a_l5>`（1.8 V）和
`qcom,wlan-sw-ctrl-gpio`；上游 `qcom,qca6390-pmu` 绑定没有对应输入（只有
`swctrl-gpios`），这次不建模。

### 为什么 `wifi@0` 必须引用 PMU 的 LDO

`drivers/pci/pwrctrl/pci-pwrctrl-pwrseq.c` 在 PCI 枚举之前就以平台设备绑定
`wifi@0`，`drivers/power/sequencing/pwrseq-qcom-wcn.c` 的
`pwrseq_qcom_wcn_match()` 通过 `vddaon-supply → 该 LDO → regulators → PMU 节点`
这条链认领消费者。所以 `regulators { ldo0..ldo9 }` 必须嵌在 `qca6390-pmu` 里，
`wifi@0` 的 `vddaon-supply` 必须指向其中的 `vreg_pmu_aon_0p59`；测试里锁住了
这一点。6.18 的 ath11k 自己不请求这些 LDO（`drivers/net/wireless/ath/ath11k/`
里没有任何 regulator 引用），它们是 PMU 内部输出，电源时序由 pwrseq 完成。

### 配置层：三处 allnoconfig 陷阱

`scripts/build/mainline/build-steamos-6.18.sh` 用 `KCONFIG_ALLCONFIG` +
`allnoconfig`，会静默丢掉依赖不满足的请求，实测踩到三个：

- `CONFIG_WLAN` 不打开时 `net/wireless/Kconfig` 根本不 source（它在
  `net/Kconfig` 的 `if WIRELESS` 里，而只有 `WLAN` 会 `select WIRELESS`），
  cfg80211/mac80211/ath11k 全部消失；
- 同理 `CONFIG_WLAN_VENDOR_ATH` 不打开时 `drivers/net/wireless/ath/Kconfig` 的
  整个菜单不可见，ath11k 拿不到；
- `CONFIG_PCI_PWRCTRL_PWRSEQ` 没有 prompt，写进片段也会被丢掉，它由
  `ATH11K_PCI` 在 `HAVE_PWRCTRL` 成立时 select，而 `HAVE_PWRCTRL` 由
  `PCIE_QCOM → PCI_PWRCTRL_SLOT` 带出来。

## 本阶段验证了什么

### 源码级

| 项目 | 结果 |
| --- | --- |
| 配置 | `build-steamos-6.18.sh config <display> <audio> <wifi>`：225 项请求全部保留 |
| 编译 | `pwrseq-qcom-wcn.o`、`pci-pwrctrl-pwrseq.o`、`pcie-qcom.o`、`ath11k/core.o`、`ath11k/mhi.o` 均 0 error |
| DTB | `cpp` + `dtc` 生成成功，节点与 phandle 链完整 |
| 仓库测试 | `tests/steamos/test_wifi_pmu.py` 全部通过 |

### 真机（2026-10-25，冷启动到 SteamOS 用户空间）

候选 BOOT 为 `out/steamos-kernel-6.18/wifi-pmu2/boot.img`（SHA-256
`72dc9b8845607d87772cb1b7e8e22885e4842d1a7f0010e98c7c33b266996d20`，71,303,168
字节），写 `/dev/sda22` 后读回哈希一致；内核 `Image` 为
`89e88f793d5344fe1fb67b8c5c917b7ffb055fd6bcd9859cf0aa65d06562d0cd`，shim 为
`c04cdc72354db6fe330d1415d6409f7289a39186c74b5939d30861f58de32343`。
镜像 = 上一轮 BOOT 的三个载荷中只换内核与 shim，ramdisk 与厂商 DTB 原样沿用
（`out/deployed/` 保存了写入前的 BOOT 与解包结果，可直接回退）。

| 观察 | 证据 |
| --- | --- |
| PCIe0 链路训练成功 | `pcieport 0000:00:00.0`，`0000:01:00.0` 上线，`current_link_speed` 5.0 GT/s（`max` 8.0） |
| 设备枚举为 QCA6390 | `/sys/bus/pci/devices/0000:01:00.0`：`0x17cb` / `0x1101`，根端口 `0x010b` |
| PMU 时序驱动绑定 | `/sys/bus/platform/drivers/pwrseq-qcom_wcn/qca6390-pmu` |
| 消费者绑定到 DT 节点 | `/sys/bus/platform/drivers/pci-pwrctrl-pwrseq/1c00000.pcie:pcie@0:wifi@0` |
| ath11k 识别芯片 | `ath11k_pci 0000:01:00.0: qca6390 hw2.0`，32 个 MSI 向量，BAR 已分配 |
| 固件缺失如预期 | `Direct firmware load for ath11k/QCA6390/hw2.0/amss.bin failed with error -2` → `failed to start mhi: -110` |

因此**电源、PCIe 和驱动绑定已经过真机确认**；`ath11k` 停在 MHI 启动，正是
本阶段预期的固件缺口（镜像里没有 `lib/firmware/ath11k`）。没有 netdev：`/sys/class/net`
只有 `lo` 和 `usb0`，所以还没有扫描、关联或 IP，这些都不能写成已完成。

启动期另见 `qcom-pcie 1c00000.pcie: Device not found`：这一行来自第一次 probe
在链路就绪前的那次检查，随后重试成功并完整枚举，不是最终失败。日志和 sysfs
快照保存在 ignored `out/wifi-6.18/`：`boot2-dmesg.txt`、`boot2-facts.txt`、
`flash-write2.txt`，上一轮无 Wi-Fi 配置的基线是 `baseline-pcie-dmesg.txt`。

### 上机才发现的配置缺口

第一次上机时 PCIe 只停在 deferred probe，`1c06000.phy` 从未 probe：原因是
`CONFIG_PHY_QCOM_QMP_PCIE` 没有开。该符号 `default PHY_QCOM_QMP`，而
`allnoconfig` 只对**有 prompt** 的符号取默认值以外的值，所以片段没写它就等于没开，
症状是没有任何错误日志、只是永远 defer。片段已补 `GENERIC_PHY` /
`PHY_QCOM_QMP` / `PHY_QCOM_QMP_PCIE`，测试也锁住了。

### 两处与操作相关的细节

- 该内核没有 `CONFIG_MAGIC_SYSRQ`，`/proc/sysrq-trigger` 不存在；主线的
  `reboot`（BusyBox）在 initramfs 里没有生效，`reboot -f` 可以。
- 写盘通道用的是设备上的 BusyBox：`nc -l -p <port>` 接收、`dd` 写 BOOT、
  `sha256sum` 双侧校验。SteamOS 用户空间起来后 `/tmp` 不存在，落到
  `/dev/shm`；`nc` 监听进程必须在 telnet 会话保持打开期间接收，否则会被 SIGHUP。

## 阶段 2：固件（已完成，真机能扫到 AP）

厂商那套固件不能直接喂给 ath11k：vendor 的 `qca6390/amss20.bin` +
`bdwlan.e05/elf*` 是 CNSS/CLD 的分文件布局，ath11k 要的是
`ath11k/QCA6390/hw2.0/{amss.bin,m3.bin,board-2.bin}`，其中 `amss.bin` 是 MHI
容器、`board-2.bin` 是带 `QCA-ATH11K-BOARD` 魔数的多板 BDF 容器。所以这一阶段
用的是 **上游 linux-firmware**，不是把 vendor blob 改名。

| 文件 | 字节数 | SHA-256（上游） |
| --- | --- | --- |
| `amss.bin` | 3,628,672 | `8ab2a06422e3989012df423bb6a156fde2c3feb4cd5db4c5d3588f674dc872ff` |
| `m3.bin` | 266,684 | `1921d25edb52f90a7a0acc7275861e14399f904d579aae9f7ab1c4d28533fad6` |
| `board-2.bin` | 231,980 | `4aa9f0fe15e9e2b6f9bb13b7187ed8ce035d9d46fb65fac5d5eba2c4e7edc091` |

来源是 `git.kernel.org` 的 `linux-firmware` main（2026-10-25 取），每个文件的
URL、大小和哈希记在 ignored `out/firmware/ath11k/manifest.json`，同目录的
`SHA256SUMS` 供 initramfs 构建时校验。与 vendor 的 `m3.bin`（266,668 字节，
`804777d7…`）**并不相同**，两者相差 16 字节：这是固件版本差异，不能混用。
BDF 也没有沿用 vendor 的 `bdwlan.e05`：实机上报的是 `chip_id 0x0` /
`board_id 0xff`，正好命中上游 `board-2.bin` 的通用项
`bus=pci,qmi-chip-id=0,qmi-board-id=255`（同 SoC 的小米平板 5 Pro 也在这份 BDF
里），所以暂不需要自己打包 board-2。

`scripts/build/mainline/build-initramfs.sh` 现在会按 `SHA256SUMS` 校验并把这
三个文件放进 initramfs 的 `lib/firmware/ath11k/QCA6390/hw2.0/`。原因和 ADSP
一样：ath11k 在 PCI probe（约 2 秒）就要固件，远早于 SteamOS rootfs 挂载。

### 真机结果

候选 BOOT `out/steamos-kernel-6.18/wifi-fw/boot.img`（SHA-256
`3510b350ef1029595b8838e4853ab53a29eebe4690f2b2d8cc9d7ed585fa6ad2`，71,303,168
字节）写入 `/dev/sda22` 后读回一致；内核与上一轮相同，只有 initramfs 变了。

| 观察 | 证据 |
| --- | --- |
| MHI 起固件 | `mhi mhi0: Power on setup success` → `Wait for device to enter SBL or Mission mode` |
| 芯片与板号 | `chip_id 0x0 chip_family 0xb board_id 0xff soc_id 0xffffffff` |
| 固件版本 | `fw_version 0x10121492 fw_build_timestamp 2021-11-04 11:23` |
| 网卡出现 | `/sys/class/net` 有 `wlan0`，MAC `f6:f0:58:8f:33:f8`，`type managed`，txpower 21 dBm |
| 实际扫描 | `iw dev wlan0 scan` 返回多个真实 BSS：5 GHz 5180 MHz（-53 dBm）与 2.4 GHz 2412 MHz（-56…-72 dBm），含 SSID/信道/信号 |

`ath11k` 的 probe 现在完整通过，没有再出现 `-110` 或板数据错误。日志在
`out/wifi-6.18/boot3-fw-dmesg.txt`、`boot3-ath11k-full.txt`、`boot3-scan2.txt`。

### 尚未验证：关联、DHCP、桌面可见

扫描只证明驱动、固件、BDF、监管域这条链可用，**不证明已连上 AP**。这一轮没有
做关联：initramfs 里没有 `iw`/`wpa_supplicant`，`nmcli` 在 init 命名空间里连不上
GUI 命名空间的 D-Bus（`Could not connect`），而 GUI 那边的 NetworkManager 是
vendor 时期那套私有构建，尚未在主线路径上验证。`rfkill0`（`phy0`）读出来是
`state 0` 但 `soft 1`，用 `ip link set wlan0 up` 可以拉起接口，扫描正常。

## 阶段 3：userspace 路径（NetworkManager 可用，可扫描）

`nmcli` 一开始把 `wlan0` 显示成 `unavailable`，日志里是
`Couldn't initialize supplicant interface: ... wpa_supplicant couldn't grab this
interface.`，重试五次后放弃。supplicant 自己的日志给出了根因：

```text
wpa_supplicant: l2_packet_init: socket(PF_PACKET): Address family not supported by protocol
```

也就是内核没有 `CONFIG_PACKET`：`allnoconfig` 不会替我们打开它，而
wpa_supplicant 的 EAPOL l2 socket 必须用 `AF_PACKET`。片段已补
`CONFIG_PACKET=y`。同一轮还发现 `cfg80211: failed to load regulatory.db`：
`regulatory.db` 属于 **wireless-regdb**，不在 linux-firmware 里，现按签名文件
`regulatory.db` + `regulatory.db.p7s`（SHA-256
`0a4abd7ae20d07bb70642937ccb2293a72a6504730eea45a698882599f586368` /
`bcd81aed039ea6b9b6f3726fbf26911a0caf4a5d894210e0fa2effb384d6b326`）一并放进
initramfs，因为 `CONFIG_CFG80211_REQUIRE_SIGNED_REGDB=y`。

顺带把 `CONFIG_MAGIC_SYSRQ=y` 加进 `steamos-6.18.config`：initramfs 里 BusyBox
的 `reboot` 不生效，之前只能靠 `reboot -f`。

### 真机结果

候选 BOOT `out/steamos-kernel-6.18/wifi-nm/boot.img`（SHA-256
`e720088753d2727b67ea87c6a17785deab70f0538d9129b41cda541cdd8a3cb4`），内核
`Image` `b3b6866e3fff6b6e2bffbf9b5bbce316931ed5d32fbebf70c5dbc99d601eef93`。

| 观察 | 证据 |
| --- | --- |
| regulatory.db 加载 | `cfg80211: Loading compiled-in X.509 certificates`，不再出现 `failed to load regulatory.db` |
| supplicant 抓起接口 | `wpa_supplicant` 常驻（PID 719），日志里不再有 `l2_packet_init` 失败 |
| NetworkManager 可用 | `nmcli device status`：`wlan0 wifi disconnected`、`p2p-dev-wlan0 wifi-p2p disconnected` |
| NM 扫描 | `nmcli dev wifi list` 列出真实 AP：5 GHz 信道 157、WPA2/802.1X、信号 100 等 |

与 init 命名空间里的 `nmcli` 连不上 D-Bus 不同，这里是通过
`nsenter -t <NetworkManager pid> -m -p -r -w -- nmcli ...` 进入 GUI 会话的
命名空间执行的；`iw`/`nmcli` 只能在那个命名空间里读到 system D-Bus。

### 仍未验证：关联与 DHCP

NM 现在处于可用状态，但**还没有关联过任何 AP**：这台设备没有保存的连接
配置（`/etc/NetworkManager/system-connections/` 不存在），而关联需要
WPA2 口令。扫描结果里出现的 `UPC` 是开放网络，但未经确认不该擅自连别人的 AP。
所以「关联、DHCP、桌面 Wi-Fi 图标」这三项保持未验收。

## 下一步（未做）

1. 关联与 DHCP：`nmcli dev wifi connect <ssid> password <psk>`（或桌面 UI）走一次，
   确认关联、DHCP、路由；vendor 那套零 gateway 路由补丁在 6.18 上不需要。
2. 蓝牙：先解决 `bt-enable-gpios` 到底是 TLMM 76 还是 21 的冲突，再走 `hci_qca`。
3. 监管域与省电：`ath11k` 的 `regdb.bin`（本机 vendor 分区里有，19,348 字节）
   目前没提供，扫描工作正常，若后续出现监管域告警再补。

vmlinux 中已有的相关驱动入口：`qcom_qca6390_pmu`（pwrseq provider）、
`pci_pwrctrl_pwrseq`（消费者）、`ath11k_pci`。
