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

| 项目 | 结果 |
| --- | --- |
| 配置 | `build-steamos-6.18.sh config <display> <audio> <wifi>`：**224 项请求全部保留** |
| 编译 | `pwrseq-qcom-wcn.o`、`pci-pwrctrl-pwrseq.o`、`pcie-qcom.o`、`ath11k/core.o`、`ath11k/mhi.o` 均 0 error |
| DTB | `cpp` + `dtc` 生成成功（125305 → 126685 字节），节点与 phandle 链完整 |
| 仓库测试 | `tests/steamos/test_wifi_pmu.py` 全部通过 |

**没有验证的**：没有真机启动，因此没有 PCIe link、没有 `lspci` 输出、没有
ath11k probe。**固件也还不在镜像里**——`amss.bin`、`m3.bin` 与匹配的 BDF 还没
进入 `lib/firmware`，所以这一阶段之后即使上机，ath11k 也应当在 firmware 加载
处失败。按项目规则，这些都不能写成硬件结果。

## 下一步（未做）

1. 从本机 vendor 逻辑分区取 QCA6390 固件，按 ath11k 的路径布局
   （`ath11k/QCA6390/hw2.0/`）落地并逐文件校验；BDF 必须来自这台的
   `bdwlan.e05`/`elf` 变体，不能用通用 blob。
2. 真机冷启动，先看 `lspci` 是否出现 `17cb:1101`、再看
   `dmesg | grep -E 'pcie|ath11k|pwrseq'`，确认电源时序与 PCIe link。
3. 再确认扫描/关联；NetworkManager 在主线 mac80211 路径上不需要 vendor 那套
   零 gateway 路由补丁。

vmlinux 中已有的相关驱动入口：`qcom_qca6390_pmu`（pwrseq provider）、
`pci_pwrctrl_pwrseq`（消费者）、`ath11k_pci`。
