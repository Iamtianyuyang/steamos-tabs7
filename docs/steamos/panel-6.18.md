# 显示路径：面板与双 VIG（主线 6.18）

主线 6.18 的显示路径完全没有在真机跑过；本文记录从厂商源码里拿到的**权威面板参数**、
6.18 树里已经存在的东西、两者之间的差距，以及按依赖排出的工作顺序。

面板参数的机器可读版本是
[hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json](../../hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json)，
由 `scripts/steamos/extract-panel-profile.py` 从厂商 4.19 树的
`techpack/display/msm/samsung/NT36523_PPA957DB1/` 直接提取（含每个源文件的 SHA-256）。
重新提取只改这一份 JSON；手工抄写参数等于制造第二份真相。

## 厂商参数（权威）

| 项 | 值 |
| --- | --- |
| 面板 | `ss_dsi_panel_NT36523_PPA957DB1_WQXGA`（Novatek NT36523 驱动 IC，厂商 CST，型号 PPA957DB1） |
| 模式 | DSI **video mode**、burst、24 bpp、`rgb_swap_rgb`、`loose` pixel packing |
| 链路 | **3 条 lane + C-PHY**（`qcom,panel-cphy-mode`），`display-topology = <2 0 2>`（两个 DSI 接口） |
| 时钟 | 998 MHz（per-link PLL） |
| 分辨率 | **每条链路 800×2560**（注释写明 `1600/2`），整块 1600×2560 面板 |
| 刷新 | 120 Hz，DFPS 列表 `120 96 60 48`（`dfps_immediate_porch_mode_vfp`） |
| Porch | H: pulse 2 / back 30 / front 84，V: pulse 1 / back 7 / front 26 |
| 物理尺寸 | 148 × 236 mm |
| 复位 | `0/12ms → 1/12ms → 0/12ms → 1/12ms`，另有 `lp11-init`、`rx-eot-ignore`、`tx-eot-append` |
| 初始化 | **359 条命令组 / 3249 字节**（`qcom,mdss-dsi-on-command`） |
| 亮度 | DCS `0x51`，4–462 级 |
| PHY 覆盖 | `phy_vreg_ctrl_0=0x50`、`phy_ctrl2=0x64`、`phy_offset_{top,mid,bot}_ctrl=0x1F`、`phy_str_swi_cal_sel_ctrl=0x04` |

最后一行很重要：厂商在这块面板上**改写了 DSI PHY 寄存器**，说明默认 PHY 调优不够用。

## 6.18 树里已有什么

- `drivers/gpu/drm/msm/disp/dpu1/catalog/dpu_6_0_sm8250.h` 有 `sm8250_dpu_caps`、`sm8250_mdp`、
  `sm8250_sspp`（含 VIG）、`sm8250_lm`——DPU 侧（含 VIG 平面）是支持的。
- `sm8250.dtsi` 有 `mdss@ae00000`、`mdss_mdp`、`mdss_dsi0/mdss_dsi1`、
  `mdss_dsi0_phy/mdss_dsi1_phy` 节点。
- `dsi_phy_7nm.c` 的 C-PHY 使能路径是完整的；项目在其上加了每面板寄存器覆写（见差距 2）。
- 上游自带的 `panel-novatek-nt36523.c` 服务于 Xiaomi elish 和 Lenovo J606F，不适用于这块
  C-PHY 面板；项目驱动是独立的 `panel-novatek-nt36523-gts7lwifi.c`。
- 项目 DTS（`sm8250-samsung-gts7-common.dtsi`）写出了完整的面板拓扑：`panel@0` 双端口 →
  `mdss_dsi0_out` / `mdss_dsi1_out`，供电、复位、ESD 引脚与 pinctrl 都已就位。

## 差距（按严重程度）

| # | 差距 | 现状 | 后果 |
| --- | --- | --- | --- |
| 1 | ~~**C-PHY**~~ | 复查后确认 `dsi_phy_7nm.c` 的 C-PHY 路径其实**已经完整**（`GLBL_CTRL` bit 6、`PEMPH_CTRL_1`、C-PHY 时序寄存器组、cphy 时序计算都在），那句 `TODO: CPHY enable path` 是过时注释，已替换为准确说明 | 无需新写使能路径；先前评估过重，这里更正 |
| 2 | ~~**PHY 调优**~~（已实现，未上真机） | 主线的 msm DSI PHY 没有"每面板覆盖寄存器"的机制。已在 `dsi_phy.c` 解析 6 个可选 DT 属性、在 `dsi_phy_7nm.c` 的 C-PHY 使能末尾应用，并在绑定里补文档；项目 DTS 的 `mdss_dsi0_phy`/`mdss_dsi1_phy` 已按提取值写入 | 值来自厂商，行为仍需真机验证 |
| 3 | ~~**初始化序列**~~（已生成） | 359 条命令由 `scripts/steamos/emit-panel-seq.py` 从面板 JSON 生成到 `panel-novatek-nt36523-gts7lwifi.h`，驱动用 `mipi_dsi_dcs_write_buffer` 原样重放（含厂商的 wait 延迟与 `0x11`/`0x29`） | 数据与厂商一致；行为待真机 |
| 4 | ~~**时序**~~（已生成） | 模式改为完整的 1600×2560@120：msm 对 bonded DSI 会把水平时序减半，所以 DRM 模式里用整幅宽度和**翻倍的**水平 porch，每链路回到厂商的 800 宽 + 84/2/30 | 时钟 570265 kHz；行为待真机 |
| 5 | ~~**lane 数与 PHY 模式**~~（已改） | DTS 现在两条 DSI 都是 `data-lanes = <0 1 2>`，两个 PHY 都带 `phy-type = <11>`（C-PHY，`PHY_TYPE_CPHY` 来自 `dt-bindings/phy/phy.h`） | 与厂商的 3 trio C-PHY 一致 |
| 6 | ~~**每链路宽度**~~（已改） | 模式按整幅 1600 宽声明，`dsi_host.c` 的 bonded 逻辑把 hdisplay/htotal 减半，于是每链路正好 800 | 与厂商语义一致 |
| 7 | ~~**物理尺寸**~~（已改） | 生成头文件里是 148×236 mm | 与厂商一致 |
| 8 | ~~**vddio 供电**~~（已补） | 从下游 `kona-sec-gts7l-display-r00.dtsi` 查到：`vddlcd`(1.8 V)=tlmm135 固定稳压器、`buck`=tlmm93 固定稳压器（10 ms 升压/放电）、复位 tlmm82；驱动改用 `vddio`/`buck` 两路 regulator | 电源时序按厂商 supply-entry 顺序 |
| 9 | **双 VIG 平面布局** | 6.18 的 catalog 有 SM8250 的 VIG，但没验证过两个 VIG 分别驱动一个 DSI 接口 | 见面板点亮后第一件事 |

## 工作顺序

1. **C-PHY 使能路径**（差距 1、2）。先在 `dsi_phy_7nm.c` 补 C-PHY 使能，并引入一个
   每面板的 PHY 覆盖入口（设备树 property 或 panel 回调），把厂商那 4 个寄存器值接进去。
   验收：PHY 锁定、无 ESD/误码日志（需要面板初始化能跑起来才看得到）。
2. **面板驱动对齐**（差距 3、4、5、6）：把 JSON 里的 359 条命令、时序、3-lane C-PHY
   写进驱动/DTS，并且**不要在参数未对齐前把 `status` 改成 `okay`**。
3. **供电**（差距 8）：确认 panel IO 的 LDO，补 `vddio-supply`。
4. 点亮后处理 **双 VIG**（差距 9）：确认两个 VIG + 两个 LM 分别绑到一个 DSI 接口，
   再复现 4.19 上的 `65,86` 平面组合。
5. 最后才是 GPU 与 SteamOS 会话侧（见 [内核迁移](kernel-6.18.md) 的阻塞项 4）。

## 边界

以上没有一项在真机上验证过。面板 JSON 里的数值是厂商 4.19 驱动的属性，可以当作
"目标值"，但不能当作"6.18 已验证"。在这块面板点亮之前，6.18 的 BOOT 不应覆盖当前
可用的 4.19 BOOT（`37f5aa13…`）。

## 已落地（仅供编译验证）

- `dsi_phy.c`：新增 6 个可选属性的解析（`qcom,cphy-vreg-ctrl-0`、
  `qcom,cphy-cmn-ctrl-2`、`qcom,cphy-rescode-offset-{top,bot,mid}`、
  `qcom,cphy-str-swi-cal-sel`），只在 C-PHY 模式下收集，缺省时保留 SoC 默认值。
- `dsi_phy_7nm.c`：新增 `dsi_phy_7nm_apply_cphy_overrides()`，在 `CMN_CTRL_0=0x7f`
  与 full-rate 选择之后按下游顺序写回这些寄存器；过时的 C-PHY TODO 注释已替换。
- `Documentation/.../dsi-phy-7nm.yaml`：补上 6 个属性的说明。
- DTS：两个 PHY 改为 C-PHY + 厂商调优值，两条 DSI 端点改为 3 trio。
- `hardware/.../configs/steamos-display-6.18.config`：显示层配置（DRM、MSM、DPU、
  DSI、7nm PHY、`DRM_PANEL_NOVATEK_NT36523_GTS7LWIFI`、backlight），`build-steamos-6.18.sh`
  支持追加 fragment 层：

```sh
scripts/build/mainline/build-steamos-6.18.sh image \
    hardware/samsung/gts7lwifi/kernel/configs/steamos-display-6.18.config
```

当前构建结果见[内核迁移](kernel-6.18.md#构建)。**编译通过不等于能点亮**。
`tests/steamos/test_dsi_phy_cphy_tuning.py` 会把 DTS 里的调优值和提取出的面板 JSON
逐一比对，防止两边漂移。

## 面板数据由代码生成（2026-10-24）

359 条初始化命令手工抄写不可审查，所以拆成"数据 = 生成、逻辑 = 手写"：

```sh
scripts/steamos/extract-panel-profile.py   # 厂商源码 -> panel/nt36523-ppa957db1.json
scripts/steamos/emit-panel-seq.py          # JSON -> panel-novatek-nt36523-gts7lwifi.h
scripts/steamos/emit-panel-seq.py --check   # 头文件是否与 JSON 同步
```

生成的 `panel-novatek-nt36523-gts7lwifi.h` 包含：

- `ppa957db1_init[359]`：`{ len, delay_ms, payload[] }`，payload 以 DCS 命令开头，
  `delay_ms` 来自厂商 `wait` 字段（有 `0x11` 后等 120 ms 的那一条）；
- `ppa957db1_mode`：1600×2560@120、`clock 570265 kHz`、htotal 1832、vtotal 2594；
- `PPA957DB1_RESET_DELAY_MS 12`、物理尺寸 148×236 mm、亮度 4–462。

驱动侧相应改成：`nt36523_send_init_sequence()` 逐条 `mipi_dsi_dcs_write_buffer`，
**不再**额外发 `0x11`/`0x29`（序列里本来就有，重复发会打乱厂商时序）；
`lanes = 3`、`MIPI_DSI_MODE_VIDEO_BURST`、`RGB888`；复位按厂商 `<0 12>,<1 12>,<0 12>,<1 12>`。
`tests/steamos/test_panel_seq.py` 会重跑生成器并逐条比对新旧数据。


## 供电与亮度（2026-10-24）

下游 display DTS 里的供电定义（`kona-sec-gts7l-display-r00.dtsi`，UTF-16 源文件）：

| 厂商 | 引脚 | 属性 | 主线实现 |
| --- | --- | --- | --- |
| `vddlcd` 1.8 V（`fixed_reg_lcd_1p8`） | tlmm135 | GPIO 固定稳压器 | `panel_vddio` regulator-fixed |
| `buck`（`fixed_reg_buck_en`，post-on 10 ms / post-off 10 ms） | tlmm93 | GPIO 固定稳压器 | `panel_buck` regulator-fixed |
| 面板复位 | tlmm82 | `qcom,platform-reset-gpio` | `reset-gpios` |
| ESD / UB con det | tlmm66 / tlmm46 | 中断检测 | 未接（见下） |

驱动改成 `vddio` + `buck` 两路 regulator，顺序按厂商 supply-entry：
`vddio → buck → 10 ms 升压 → 复位四相 → 初始化序列`，下电反向并保留 10 ms 放电。

亮度用生成头文件里的 `PPA957DB1_BRIGHTNESS_MAX/MIN`（462/4）注册 backlight 设备，
`update_status` 走标准 `mipi_dsi_dcs_set_display_brightness()`（DCS `0x51`，两字节）。
绑定文档 `samsung,gts7l-nt36523.yaml` 记录了 `reset-gpios`、两路 supply 与双 DSI 端口。


## Bonded DSI 与双 VIG 的对应关系（2026-10-24）

厂商拓扑是 `qcom,display-topology = <2 0 2>`：**两个 DSI 接口并联驱动一块面板**，
每条链路 800 宽。主线要表达同一件事必须显式声明 bonding，否则两条 DSI 会被当成
两块独立面板。DTS 已补：

```dts
&mdss_dsi0 { qcom,dual-dsi-mode; qcom,master-dsi; };   /* 主链路，共用时钟，命令下发到两侧 */
&mdss_dsi1 { qcom,dual-dsi-mode; };
```

对应的驱动行为（`dsi_manager.c` / `dsi_host.c`）：`is_bonded_dsi` 为真时
`hdisplay/htotal/hsync_*` 全部减半（每链路回到 800 + 84/2/30），DCS 命令只发一次就
同时驱动两条链路。`qcom,sync-dual-dsi` 没有打开——它是"两半画面撕裂"时的备选，
打开会走同步命令路径，需要真机证据才能确定，代码里刻意保持关闭并有测试守着。

**双 VIG** 在主线是 DPU 的 split display：一个 CRTC → `dpu_encoder_virt` →
两个 `dpu_encoder_phys_vid`（`ENC_ROLE_MASTER` / `ENC_ROLE_SLAVE`）→ `INTF0`/`INTF1`
→ DSI0/DSI1，每半各自绑一组 LM + SSPP（VIG）。这与厂商 `65,86` 那对 VIG 平面是同一
件事的两种表达，但主线的平面/混频器分配由 catalog 自动完成（`sm8250_vig_sblk_0..3`
在 6.18 的 `catalog/dpu_6_0_sm8250.h` 中），不靠用户态补丁强行指定。

要点：4.19 上 wlroots 的 `WLR_DRM_SM_T870_SPLIT=65,86` 是**用户空间补丁**绕开厂商
DPU 的限制；主线应当在 DPU 内部完成拆分，wlroots/gamescope 只需按普通双链路
面板处理。这一点必须先点亮才能证实，不能预先宣称。

## ESD（2026-10-24）

厂商用两根无上下拉的输入做面板状态检测：`esd-irq-gpio1` = tlmm66（上升沿）、
`ub-con-det` = tlmm46。驱动现在按可选属性 `esd-gpios` / `ub-con-det-gpios` 申请
线程化 IRQ（上升沿），触发后进入 `nt36523_esd_work()`：

1. 只在 `prepared` 状态下恢复（否则忽略）；
2. 断电 → 重新上电（含厂商的 10 ms 升压与四相复位）→ 重放 359 条初始化序列；
3. 失败则标记未准备并报错，成功则累加 `esd_recoveries` 并打日志。

`prepare`/`unprepare` 与 ESD 恢复共用一把 mutex，`unprepare`/`remove` 会
`cancel_work_sync()`，避免恢复流程与下电交叉。

## 显示链路已打开（2026-10-24）

DTS 里 `mdss`、`mdss_mdp`、`mdss_dsi0`、`mdss_dsi1`、两个 PHY 和 `panel@0` 全部改为
`status = "okay"`，面板节点带上厂商的 pinctrl 状态（reset gpio82 8 mA 无上下拉、
ub-con-det gpio46 2 mA 输入、esd gpio66 8 mA 输入，均 `bias-disable`）。6.18 BOOT 候选
会检查 DTB 里确实存在 `samsung,gts7l-nt36523` 节点。

## 仍未完成（都需要真机）

- 面板从未点亮：C-PHY 锁定、359 条初始化序列、时序与双链路同步都只有编译证据；
- `qcom,sync-dual-dsi` 是否需要打开，要看真机上两半是否撕裂；
- 双 VIG 的 DPU split 分配需要在点亮后用 `dmesg`/帧内容确认左右半屏对应关系；
- ESD 恢复路径只在真机上才能验证（触发条件、恢复后画面）；
- 亮度 `update_status` 与面板实际亮度响应需要看一眼。
