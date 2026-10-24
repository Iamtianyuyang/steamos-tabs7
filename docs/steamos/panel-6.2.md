# 6.2 显示路径评估：面板与双 VIG

主线 6.2 的显示路径完全没有在真机跑过；本文记录从厂商源码里拿到的**权威面板参数**、
6.2 树里已经存在的东西、两者之间的差距，以及按依赖排出的工作顺序。

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

## 6.2 树里已有什么

- `drivers/gpu/drm/msm/disp/dpu1/dpu_hw_catalog.c` 已包含 `sm8250_dpu_caps`、`sm8250_mdp`、
  四组 `sm8250_vig_sblk_*`——DPU 侧（含 VIG 平面）在 6.2 是支持的。
- `sm8250.dtsi` 已有 `mdss@ae00000`、`mdss_mdp`、`dsi0/dsi1`、`dsi0_phy/dsi1_phy` 节点。
- 项目 DTS 已写出面板连接拓扑（`sm8250-samsung-gts7-common.dtsi`）：`panel@0` 双端口
  → `dsi0_out` / `dsi1_out`，GPIO `reset=tlmm82`、`ldo-en=tlmm135`、`buck-en=tlmm93`，
  **全部 `status = "disabled"`**，`vddio-supply` 仍是 TODO。
- `panel-novatek-nt36523.c` 已有 `samsung,gts7l-nt36523` 兼容项和 PPA957DB1 描述，
  但**初始化序列是占位**（只有 `FF 10`、`FB 01` 两条，文件里自己写着 "panel will not
  light up until the real sequence is populated"），时序也是估算值（`clock 268316`、
  HFP 96/HSW 16/HBP 64、60 Hz，注释标 TODO）。

## 差距（按严重程度）

| # | 差距 | 现状 | 后果 |
| --- | --- | --- | --- |
| 1 | **C-PHY** | `dsi_phy_7nm.c` 有 `cphy_mode` 分支和 C-PHY PLL 计算，但使能路径仍写着 `/* TODO: CPHY enable path (this is for DPHY only) */` | 面板用的是 C-PHY，D-PHY 的使能序列不会生效 |
| 2 | **PHY 调优** | 主线的 msm DSI PHY 没有"每面板覆盖寄存器"的机制，厂商值（0x50/0x64/0x1F/0x04）无处安放 | 即使 C-PHY 通了，PHY 也可能锁不住/误码 |
| 3 | **初始化序列** | 主线驱动 2 条占位 vs 厂商 359 条 | 面板不会亮 |
| 4 | **时序** | 主线 2560×1600@60、HFP96/HSW16/HBP64；厂商 800×2560@120、HFP84/HSW2/HBP30 | 模式与面板不匹配，可能直接黑屏 |
| 5 | **lane 数与 PHY 模式** | DTS `data-lanes = <0 1 2 3>`（4 lane D-PHY）；厂商 3 lane C-PHY | 组合非法 |
| 6 | **每链路宽度** | 主线注释写"每条 DSI 送完整 2560"；厂商是每条 800（=1600/2） | 双 DSI 拆分方式错误 |
| 7 | **物理尺寸** | 主线 220×138 mm；厂商 148×236 mm | 仅影响 DPI/触摸标定 |
| 8 | **vddio 供电** | TODO | 面板 IO 无电源 |
| 9 | **双 VIG 平面布局** | 6.2 有 `sm8250_vig_sblk_*`，但没验证过两个 VIG 分别驱动一个 DSI 接口 | 见面板点亮后第一件事 |

## 工作顺序

1. **C-PHY 使能路径**（差距 1、2）。先在 `dsi_phy_7nm.c` 补 C-PHY 使能，并引入一个
   每面板的 PHY 覆盖入口（设备树 property 或 panel 回调），把厂商那 4 个寄存器值接进去。
   验收：PHY 锁定、无 ESD/误码日志（需要面板初始化能跑起来才看得到）。
2. **面板驱动对齐**（差距 3、4、5、6）：把 JSON 里的 359 条命令、时序、3-lane C-PHY
   写进驱动/DTS，并且**不要在参数未对齐前把 `status` 改成 `okay`**。
3. **供电**（差距 8）：确认 panel IO 的 LDO，补 `vddio-supply`。
4. 点亮后处理 **双 VIG**（差距 9）：确认两个 VIG + 两个 LM 分别绑到一个 DSI 接口，
   再复现 4.19 上的 `65,86` 平面组合。
5. 最后才是 GPU 与 SteamOS 会话侧（见 [内核迁移](kernel-6.2.md) 的阻塞项 3）。

## 边界

以上没有一项在真机上验证过。面板 JSON 里的数值是厂商 4.19 驱动的属性，可以当作
"目标值"，但不能当作"6.2 已验证"。在这块面板点亮之前，6.2 的 BOOT 不应覆盖当前
可用的 4.19 BOOT（`37f5aa13…`）。
