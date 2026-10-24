# Galaxy Tab S7 Wi-Fi (SM-T870 / gts7lwifi) → 主线 Linux 设备树移植清单

这是一份早期草稿记录，勾选项不能视为真机验收。当前构建使用
`linux-sm8250-v6.2/arch/arm64/boot/dts/qcom/`；早期独立 DTS/DTSI 已归档到
`hardware/samsung/gts7lwifi/devicetree/drafts/`，下游参考保存在同级 `vendor/`。
下文的树形结构和 include 名称描述内核源码内部的建议布局。

> 目标：基于 `sm8250-mainline` / 上游内核，为 gts7lwifi 写一份可启动的 mainline 设备树。
> 参考机型：**Xiaomi Mi Pad 5 Pro（codename elish）** —— 同 SoC、同 PMIC、同 WLAN 芯片、同面板控制器，是现成最近的模板。
> 数据来源：downstream = LineageOS `android_kernel_samsung_sm8250` (lineage-23.2) `arch/arm64/boot/dts/samsung/gts7l/`；mainline = `torvalds/linux` `arch/arm64/boot/dts/qcom/sm8250-xiaomi-elish-*`。

---

## 0. 几个对你极其有利的"撞型号"

| 部件 | gts7lwifi（downstream 实测） | elish（mainline 已支持） | 结论 |
|---|---|---|---|
| SoC | SM8250 (kona) v2.1 | SM8250 | **完全一致**，SoC 级 dtsi 直接 include |
| PMIC | pm8150 / pm8150b / pm8150l / pm8009 | 同上 | **完全一致**，PMIC dtsi 直接复用 |
| WLAN/BT | **QCA6390**（走 pcie0） | QCA6390 | 驱动+绑定现成，只改 GPIO |
| 屏幕控制器 | **Novatek NT36523**（NT36523_PPA957DB1，WQXGA 2560×1600 video，dual-DSI） | novatek,nt36523（BOE/CSOT 变体） | **mainline 已有 panel 驱动**，只需加你这块的时序 |
| 触摸 | Novatek nvt-ts @ i2c 0x62 | （elish 主线缺触摸） | 有 mainline nvt-ts 驱动可参考 |
| 存储 | UFS + microSD(sdhc_2) | UFS | UFS 直接复用 |

> 因为是 **Wi-Fi 版（无 modem）**，可以直接 `/delete-node/` 掉所有 modem/mdm 相关，省掉高通移植里最痛的一块。

---

## 1. 文件骨架（建议结构）

```
arch/arm64/boot/dts/qcom/
├── sm8250-samsung-gts7lwifi.dts      # 顶层：model/compatible + include 下面两个
└── sm8250-samsung-gts7-common.dtsi   # gts7 系列共用（gts7l/gts7xl 都能 include）
```

顶层 `.dts` 最小内容（照抄 elish-boe.dts 改）：
```dts
/dts-v1/;
#include "sm8250-samsung-gts7-common.dtsi"
/ {
    model = "Samsung Galaxy Tab S7 Wi-Fi";
    compatible = "samsung,gts7lwifi", "qcom,sm8250";
};
```
别忘了在 `arch/arm64/boot/dts/qcom/Makefile` 加一行 `dtb-$(CONFIG_ARCH_QCOM) += sm8250-samsung-gts7lwifi.dtb`。

---

## 2. 按 bring-up 顺序的节点清单

### 阶段 A — 让它不 panic 地跑起来 + 拿到 log（最优先）
- [x] **根节点 / chassis** ：`chassis-type = "tablet";`，`qcom,msm-id = <QCOM_ID_SM8250 0x20001>`（v2.1），`qcom,board-id`（板号待从 downstream/bootloader 确认，当前占位 `0x1000b 0`）。
- [x] **include 链**：`sm8250.dtsi` + 四个 PMIC（pm8150/b/l/8009）—— 直接抄 elish-common 头部。
- [x] **reserved-memory**：`/delete-node/` mpss + 重定义（adsp_mem / cdsp_secure_heap / xbl_aop_mem）。**地址来自 downstream，需真机核实**。
- [x] **ramoops/pstore**：`@0xb0000000` 4 MB，console+pmsg 各 256 KB。
- [x] **chosen / framebuffer**：simplefb `@0x9c000000`，2560×1600 ARGB8888。
- [x] **aliases + uart6**（debug uart，能接到就接）。

### 阶段 B — 能 SSH 进去调试
- [x] **`&usb_1` / `&usb_1_dwc3` / `&usb_1_hsphy` / `&usb_1_qmpphy`**：peripheral 模式，vdda 供电抄 elish。
- [x] **`&ufs_mem_hc` / `&ufs_mem_phy`**：抄 elish，挂上内置存储。
- [x] **`&pon_pwrkey` / `&pon_resin`**：电源/音量下键（PON）。

### 阶段 C — 交互三件套
- [x] **gpio-keys**：`&pm8150l_gpios 3 GPIO_ACTIVE_LOW`（音量下）。
- [x] **触摸 Novatek**：`novatek,nvt-ts` @ i2c5（QUP5，GPIO12/13），IRQ=tlmm15，reset-gpio TODO（待下游核实）。
- [ ] **充电/电量**：⚠️ **与 elish 不同**。gts7l 用 **max77705**（main charger，含 Maxim 电量计）+ **pca9468**（direct charger, i2c 0x57）。i2c 总线号待下游核实，已留注释占位。

### 阶段 D — 显示（最难，单独拎出来见第 3 节）
- [x] `&mdss` / `&dsi0(+phy)` / `&dsi1(+phy)` / DSI out endpoint —— dual-DSI 骨架已接线，dsi1 slave 时钟指向 dsi0_phy。
- [x] `&gpu` / `&gmu` / zap-shader —— firmware-name 已设 gts7lwifi 路径。
- [x] panel 节点（NT36523）：**`panel-novatek-nt36523.c` 已写**，Kconfig/Makefile 已加。dual-DSI probe 参照 nt35597 方式（port@0→dsi0, port@1→dsi1）。reset=tlmm82, ldo-en=tlmm135, buck-en=tlmm93。**DSI init sequence 为 placeholder，需从 downstream techpack 填入真实命令；`drm_display_mode` 时序待核实。**

### 阶段 E — 无线 / 外设
- [x] **`&pcie0` / `&pcie0_phy`**：已启用；wlan-en=tlmm90, bt-en=tlmm76 在 pinctrl 中声明，QCA6390 通过 PCIe 自动枚举。
- [ ] **`&pm8150b_typec` / `&pm8150b_vbus`**：USB-C。⚠️ CC/PD 可能走 max77705；DP 有 ps5169 redriver（i2c @0x28）——先不做，只保持 USB2 peripheral。
- [x] **`&sdhc_2`**：cd=tlmm77，vmmc=vreg_l9c_2p7，vqmmc=vreg_l6c_2p9，pinctrl 已配。
- [x] **`&adsp` / `&cdsp` / `&slpi`**：firmware-name 路径 `qcom/sm8250/gts7lwifi/`，venus 留到音频阶段。
- [ ] **音频**（WCD938x + lpass）：留到最后。

### 阶段 F — 锦上添花（可长期搁置）
- [ ] S Pen / Wacom（`wacom,w90xx` @0x56, irq tlmm136, avdd=pm8150_l13/3.3V, max_coords 22656×14160）
- [ ] Grip 传感器（abov a96t3x6 @ i2c 0x20）、指纹、GPS(bcm47531)、相机、Pogo 键盘（stm32 @0x2a）。

---

## 3. 屏幕 panel（NT36523）——重点攻坚

downstream 关键事实（从 `kona-sec-gts7l-display-r00.dtsi` + techpack 提取）：
- 控制器：**Novatek NT36523**，面板型号 `NT36523_PPA957DB1`，**WQXGA 2560×1600，video 模式，dual-DSI**。
- 相关 GPIO（tlmm）：`reset/dsi = gpio82`、`UB/ESD det = gpio46`、`LDO enable = gpio135`、`buck enable = gpio93`。
- ESD：`samsung,ub-con-det = <&tlmm 46>`。
- 供电：用 fixed-regulator + 上述 enable GPIO（不是 elish 的 ktz8866 背光路线）。

移植策略：
1. 先看 mainline `drivers/gpu/drm/panel/panel-novatek-nt36523.c` 是否已支持接近的面板 —— elish 的 `xiaomi,elish-boe-nt36523` / `xiaomi,elish-csot-nt36523` 都在里面。
2. 大概率需要**新增一个 panel compatible**（如 `samsung,gts7l-nt36523`）并填入你这块的 DSI 初始化序列与时序（porch/clock）。初始化序列要从 downstream 的 `dsi_panel_NT36523_PPA957DB1_wqxga_video.dtsi`（techpack）里扒 `qcom,mdss-dsi-on-command` 那串十六进制命令翻译过来。
3. dual-DSI：`&mdss_dsi0` 设 `qcom,master-dsi` + `qcom,sync-dual-dsi`，`&mdss_dsi1` 跟随 —— 直接照 elish-common 的 dsi 段改。
4. 背光：NT36523 支持 DCS 背光，优先用 panel 内置 DCS 背光；若不行再处理外部 bias。

---

## 4. 与 elish 的"不能照抄"清单（重点核对）

| 项 | elish | gts7lwifi | 处理 |
|---|---|---|---|
| 电量计 | ti,bq27z561 | Maxim max77705 内置 | 换驱动，可能要补 mainline 支持 |
| 充电 | （elish 简单） | max77705 + pca9468 直充 | 先只做"能识别在充电"，直充后做 |
| USB-C PD | pm8150b_typec | 可能 max77705 CC | 先做 USB2 数据，PD/DP 后做 |
| 背光 | kinetic,ktz8866 (i2c11) | fixed-reg + DCS | 用 panel DCS 背光 |
| microSD | （elish 无） | sdhc_2 有 | 需自己加 sdhc_2 节点 |
| panel 变体 | BOE/CSOT | PPA957DB1 | 新增 compatible + 时序 |

---

## 5. 待在真机上确认的未知量
- [ ] `qcom,board-id` 真实值（从 downstream board 文件或 bootloader log 抓）。
- [ ] NT36523 的精确 DSI 时序 / lane 数 / clock（从 techpack 命令序列推）。
- [ ] max77705 在 mainline 的支持程度（电量计/charger/CC 哪几块可用）。
- [ ] reserved-memory 各段的确切基址/大小（对错直接决定能不能开机）。

---

## 6. 下一步动作建议
1. fork `sm8250-mainline/linux`，把上面"文件骨架"建好，先只做阶段 A，目标：**ramoops 里看到内核跑过 SMP bringup**。
2. 阶段 B 起 USB networking，之后所有调试走 SSH。
3. 把 panel 当成独立子任务：先让 `&mdss` + simplefb 出画面，再上 NT36523 DSI 命令序列。
4. 全程对照 elish-common.dtsi 逐节点搬，改 GPIO/regulator/i2c 编号即可。

> 参考链接：
> - 上游 elish 模板：`arch/arm64/boot/dts/qcom/sm8250-xiaomi-elish-common.dtsi`（torvalds/linux）
> - downstream gts7l 全套：LineageOS `android_kernel_samsung_sm8250`，路径 `arch/arm64/boot/dts/samsung/gts7l/`
> - SM8250 移植协调：postmarketOS SM8250 wiki + sm8250-mainline GitHub org
> - 方法论范本：postmarketOS "SDM845 Mainlining" wiki（把 845 换成 8250 套用）
