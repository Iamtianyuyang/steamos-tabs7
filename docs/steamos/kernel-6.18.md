# 内核迁移：厂商 4.19 → 主线 6.18 LTS

SteamOS 的目标内核是主线 **6.18 LTS**（`linux-sm8250-v6.18/`，基线 6.18.58）。
**6.18 已在真机启动；冷启动无需手动操作即显示 Steam 界面，#51 触摸在 Steam 内生效（各一次实测），重复启动与完整输入仍待验收。**
2026-10-25 的显示复测中，基线与仅补 display-on 的候选均被用户确认黑屏。
4.19 回退已恢复过完整 Steam 画面；当前 6.18 PLL 修正版已通过全屏彩色测试图，实际会话与启动仍待验收，详见[显示恢复记录](display-recovery.md)。
这份文档记录构建方式、配置映射和各项独立验收；连接器 `connected`、进程 `active`
和 GPU 枚举成功都不能替代实际屏幕出图。

## 为什么是 6.18

上游事实于 2026-10-24 联网核对。

| 需求 | 最低内核 | 4.19 | 6.18 | 来源 |
| --- | --- | --- | --- | --- |
| systemd 258+（去掉 cgroup v1） | 5.4（推荐 5.7，下个版本预计 5.10） | ⛔ | ✅ | systemd v258 发布说明 |
| Proton fsync（`futex_waitv`） | 5.16 | ⛔ 只能用 esync | ✅ | Phoronix，5.16 合并窗口 |
| ntsync（Wine 10.15+） | 6.14 | ⛔ | ✅ | Phoronix，6.14 char/misc |
| `msm` VM_BIND（Vulkan sparse） | 6.17 | ⛔ | ✅ | 6.17 周期的 DRM pull；CVE-2026-53054 受影响范围从 6.17 起 |
| Turnip sparse binding/residency | 需要上面的 VM_BIND | ⛔ | ✅ | Mesa !32533，2025-08-25 合入 |
| 上游 Mesa / SteamOS 自带 Mesa | 主线 `msm` | ⛔ KGSL | ✅ | 见下文“能带过去的与带不过去的” |

- 6.12 和 6.18 两个 LTS 的维护期都已延长到 2028-12。6.18 是唯一同时满足上表所有项的 LTS。
- 6.18 收录了 Galaxy S20（`x1q`）和 S20 FE（`r8q`），两者共用 `sm8250-samsung-common.dtsi`。
  这说明主线内核在三星 SM8250 的启动链上能启动。这个 dtsi 只含 simple-framebuffer、按键、
  ramoops、PM8150 稳压器、USB、UFS，是对照 `sm8250-samsung-gts7-common.dtsi` 的参考。
  主线没有 Tab S7 自己的设备树，也没有它的面板、触摸或 GPU 节点。

### 时间约束：systemd 258

当前 SteamOS rootfs 是 **systemd 257.7**（`out/steamos/systemd-facts.txt`），还能在
4.19 上运行。一旦 rootfs 升级到 258，**4.19 路径就无法启动**。因此在 6.18 通过真机
验收之前，更新 rootfs 前要先确认 systemd 主版本 < 258。

## 构建

```sh
scripts/build/mainline/build-steamos-6.18.sh config   # 只验证配置
scripts/build/mainline/build-steamos-6.18.sh dtb      # DTB
scripts/build/mainline/build-steamos-6.18.sh image    # DTB + Image/Image.gz（默认）
scripts/build/mainline/build-steamos-6.18.sh image \
    hardware/samsung/gts7lwifi/kernel/configs/steamos-display-6.18.config  # 追加显示层
```

脚本把 `hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config` 与
`steamos-6.18.config` 合并（后者覆盖前者），用 `allnoconfig` 生成配置，
**逐条核对每个请求的符号是否真的保留**，然后把 DTB、`Image`、`Image.gz`、
`kernel.config`、`config-review.txt` 和 `manifest.json` 写到
`out/steamos-kernel-6.18/`。缓存目录是
`~/.cache/tabs7linux/steamos-6.18-<项目路径哈希>/`（`TAB_S7_STEAMOS_BUILD_ROOT` 可覆盖）。

2026-10-24 构建记录（历史产物；当前诊断镜像见显示恢复记录，6.18.58，clang 18.1.3 / LLD 18）：

| 产物 | SHA-256 |
| --- | --- |
| `Image` | `d8883a29c8ddd831fea40a82694c45c586a9d83153033e2642d944adc9625631` |
| `Image.gz` | `62e90443afa5a39a763c5f49fd51fc06808ee6d4838c912d27e1e1b904a99f5c` |
| `sm8250-samsung-gts7lwifi.dtb` | `92e5d160b8ae25864ab66a93e86f84a6653ff95008d4f621d201f174138bb0e9` |

130 项请求全部保留。产物只是编译结果，不是可启动镜像。本机没有 `dtschema`，
DTS 还没跑过 `dtbs_check`。

## 源码树

`linux-sm8250-v6.18/` 是 kernel.org 的 `linux-6.18.58.tar.xz`（SHA-256
`069b19f9…cbdecb1`，校验列表经 kernel.org autosigner `B886…A6B1` 签名验证），加上
下面这些项目改动。与原版 6.18.58 做 diff 就能列出全部改动：

| 改动 | 文件 |
| --- | --- |
| 板级 DTS | `arch/arm64/boot/dts/qcom/sm8250-samsung-gts7lwifi.dts`、`sm8250-samsung-gts7-common.dtsi`，dtb `Makefile` 条目，`bindings/arm/qcom.yaml` 加 `samsung,gts7lwifi` |
| C-PHY 每面板寄存器覆写 | `drivers/gpu/drm/msm/dsi/phy/dsi_phy.{c,h}`、`dsi_phy_7nm.c`，`bindings/display/msm/dsi-phy-7nm.yaml` |
| 面板驱动 | `drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.{c,h}`，Kconfig `DRM_PANEL_NOVATEK_NT36523_GTS7LWIFI`，`bindings/display/panel/samsung,gts7l-nt36523.yaml` |

从早先主线树移植时做的适配：

- **DSI 标签改名**：6.18 的 `sm8250.dtsi` 把 `dsi0`/`dsi0_phy`/`dsi0_out`（dsi1 同理）改成了
  `mdss_dsi0`/`mdss_dsi0_phy`/`mdss_dsi0_out`，板级 DTS 和测试已跟着改。
- **面板驱动改名**：6.18 自带一个 `panel-novatek-nt36523.c`（Xiaomi elish、Lenovo J606F），
  与项目驱动同名。项目驱动改为 `panel-novatek-nt36523-gts7lwifi.c`，驱动名和 Kconfig 都独立，
  上游的 `DRM_PANEL_NOVATEK_NT36523` 保持关闭。
- **面板驱动 API**：改用 `devm_drm_panel_alloc()`，并设置 `prepare_prev_first`（`prepare()`
  里要发初始化序列，DSI host 必须先上电）。旧写法在 `drm_panel_init()` 之前就用
  `ctx->panel.dev` 注册背光和 ESD 中断，那时它还是 NULL，改为一开始分配后这个问题消失。
  Kconfig 补上了 `depends on BACKLIGHT_CLASS_DEVICE`。
- **PHY 寄存器写法**：6.18 去掉了 `dsi_phy_write()`，覆写函数改用 `writel()`。
- **binding**：6.18 已经自带 `qcom,master-dsi`、`qcom,sync-dual-dsi` 的说明，不再重复添加。
- 早先主线树里随带的 `sm8250-lenovo-j716f.dts` 和 `configs/sm8250.config` 不属于本项目，没有带过来。

## 配置映射

| 4.19 厂商片段 | 6.18 片段 | 说明 |
| --- | --- | --- |
| `steamos-ipc.config`：`SYSVIPC`、`IPC_NS` 关闭 | `SYSVIPC`、`SYSVIPC_SYSCTL`、`SYSVIPC_COMPAT`，`IPC_NS` 仍关闭 | Steam 客户端调用 `semget()`；`IPC_NS` 沿用 4.19 的决定，尚未在 Steam Runtime 里验证过开启 |
| `steamos-systemd.config`：`DEVTMPFS`、`FHANDLE`、`CGROUP_PIDS`、`USER_NS`、`PID_NS`、`AUTOFS_FS` | 同名符号，追加 `CGROUPS`、`NAMESPACES`、`NET_NS`、`UTS_NS`、`TMPFS_XATTR`、`TMPFS_POSIX_ACL` | `allnoconfig` 会关掉 `CGROUPS`/`NAMESPACES`，而 `CGROUP_PIDS`/`USER_NS` 依赖它们，不显式打开就会被 Kconfig 静默丢弃 |
| `DEVTMPFS_MOUNT` 关闭 | 同样关闭 | initramfs 自己在新根上挂 devtmpfs，内核先挂会打架 |
| 厂商内核内建 F2FS | `F2FS_FS=y` | 交接步骤要先挂 userdata 才能找到 SteamOS 树，主线默认没有 |

## 能带过去的与带不过去的

| 组件 | 4.19 做法 | 6.18 上的状态 |
| --- | --- | --- |
| systemd / cgroup v2 / logind | 4.19 + 补的 `DEVTMPFS`、`FHANDLE`、`CGROUP_PIDS`、`USER_NS`/`PID_NS` | 主线原生支持，配置已就位 |
| HCI socket | 厂商内核禁用了它，需要 `hci-socket-restore.patch` 重建内核 | 主线本来就启用，补丁不需要 |
| 热插拔节点 | 厂商没有 `DEVTMPFS`，靠手工 `mknod` + 写 `uevent` | 主线有 `DEVTMPFS`，`systemd-udevd` 正常建节点 |
| VT / seatd 变通 | 厂商内核无 VT，用 `SEATD_VTBOUND=0` | 主线有 VT/DRM，变通可保留但不再是必需 |
| GPU（Turnip KGSL → Zink → wlroots/gamescope） | 私有 Mesa 走厂商 `KGSL`，双 VIG 由私有补丁拆分 | **主线没有 KGSL**，换成上游 `msm` + freedreno/Turnip。私有前缀 `turnip-kgsl/`、`mesa-zink*`、`gamescope-sm-t870` 的构建前提都变了 |
| 面板与双 VIG | 厂商 DSI/DPU 行为 + `WLR_DRM_SM_T870_SPLIT=65,86` | 面板驱动是项目自带的；主线应在 DPU 内部完成左右拆分，见[面板与双 VIG](panel-6.18.md) |
| 触摸 | 厂商 `sec_touchscreen`/nt36523 | 主线 `novatek-nvt-ts` 加 `novatek,nt36523-ts` 与面板跟随，协议相同；#51 在 Steam 内可点击，见[触摸记录](touch-6.18.md) |
| Wi-Fi / 蓝牙 | 厂商 `qcacld` + CNSS + 私有 NetworkManager | 主线路线是 `ath11k` + `hci_qca`，与现有 `userspace/platform/network/`、`platform/bluetooth/` 的适配对象不同。主线已真机确认 PMU 时序、PCIe0 链路（`17cb:1101`）、ath11k + 上游固件与 `wlan0` 扫描；关联/DHCP 未验收，见[主线 Wi-Fi](wifi-6.18.md) |

## 真机启动记录（2026-10-24）

**状态：6.18.58 已在真机上启动到 initramfs `/init`，屏幕输出内核日志；USB ECM/ACM 与 telnet 已通，尚未进入持久 SteamOS。**
证据是平板屏幕照片（fbcon 输出）、主机侧 `lsusb`/内核日志，以及 telnet 内的 `uname` 和 MUIC 读回值。

### 启动链：三星 ABL 只接受厂商 DTB，内核可以随便换

| 实验（DTBO、vbmeta 均为 Lineage 原版） | 结果 |
| --- | --- |
| 184 字节探针（`boot/probe/entry-probe.S`，涂红 splash 并喂狗）+ 厂商 DTB | ✅ 屏幕变红，不落 Download |
| 同一探针 + 主线 DTB | ⛔ 约 26 s 落入 `Custom Binary(boot) Blocked By OEM Lock` |

- 拦截的是 BOOT 里的**主线 DTB**，内核内容不受校验。以前实验 A/E1 和 6.18 的所有失败都是这一个原因，
  不是看门狗，也不是内核崩溃。
- 所以 BOOT 里放的是 **shim**（`boot/shim/`，`scripts/build/mainline/build-boot-shim.sh`）：ABL 看到的
  是厂商 DTB（`out/steamos/boot-preflight-*/boot/original/dtb`，3 份拼接）+ Lineage DTBO，shim 把 ABL
  填好的 `/chosen/bootargs`、`linux,initrd-*`、`kaslr-seed`、`/memory/reg` 搬进内嵌的主线 DTB，把内嵌
  Image 放到 2 MiB 对齐处后跳入。shim 出错时用整屏颜色报告（黄/品红/青），成功前画绿条。
  打包：`build-steamos-6.18-boot.py --experiment --kernel out/boot-shim/Image --dtb <厂商 DTB>`。
- 这样 DTBO 和 vbmeta 永远保持 Lineage 原版，4.19 与 6.18 之间切换只换 BOOT。
- splash：`0x9c000000`，1600×2560，XRGB8888，探针涂红已验证；`simple-framebuffer` 按此启用。

### 今天确认的其他事实

- **DTBO 必须和 vbmeta 的 dtbo 哈希一致**：重建的 DTBO（无 footer、或用测试密钥签 footer）都被拦，
  Odin 也报 `FAIL! Auth`。`build-noop-dtbo.py`、`build-vbmeta.py` 能生成配对的 DTBO+vbmeta，
  但走 shim 后不再需要。vbmeta 分区（`sdd25`）在 UFS 层写保护，TWRP 里 `dd` 报只读，只能用 Odin。
- **Odin 的 Auth 结果在"启动被拦后自动进入的 Download 会话"里不可信**：同一会话里连 4.19 基线
  BOOT 也报 Auth；从 TWRP `adb reboot download` 主动进入的会话正常。Auth 失败后会话即失效。
- 基础 DTB 的 `qcom,board-id` 是 `0 0`（厂商同样如此），`8 7` 是 ABL 叠加 DTBO 第 6 项之后的值。
- 设备 DTBO 曾经停在实验 E1 的空表（`dd04ec36…`），已恢复为 Lineage `9383ef37…`。

### 6.18 早期初始化：依次查清的三个问题

1. **SoC 基础驱动缺失**（屏幕上 `devices_deferred`）：`apps_rsc` 依赖 PSCI 空闲电源域，缺它则
   rpmhcc、bcm-voter、RPMh 稳压器都不出现，interconnect、USB PHY、dwc3 全部停在 defer；TLMM、SPMI
   依赖 PDC；AOSS 依赖 IPCC。bring-up 配置已补 `CPU_IDLE`、`ARM_PSCI_CPUIDLE(_DOMAIN)`、`QCOM_PDC`、
   `QCOM_IPCC`；命令行用 `cpuidle.off=1` 保留电源域但不进深度空闲。
2. **TLMM probe 挂死**（`dyndbg="file drivers/base/dd.c +p"` 显示最后一行是 sm8250-pinctrl）：
   6.18 的 gpiolib 在注册时读取每个引脚的方向（`gpiolib.c:1172`），4.19 不读。GPIO 20–23 是 TrustZone
   的指纹 SPI，三星内核用 `msm_gpio_is_valid()` 跳过它们（真机 `/proc/config.gz`：
   `SENSORS_FP_SPI_GPIO_START/END=20/23`，无 eSE、MST）。改为 `gpio-reserved-ranges = <20 4>, <40 4>`，
   与上游 Galaxy S20 一致。
3. **dwc3 `failed to enable ep0out`**：只接了 USB2 PHY，没有 PIPE 时钟。`&usb_1` 加
   `qcom,select-utmi-as-pipe-clk;` 后消失（与上游 S20 一致）。

另：面板两路固定稳压器加 `regulator-boot-on`（没有它，TLMM 正常后 `fixed.c` 会以 OUT_LOW 关掉面板）；
SPMI SID 2/0xa/0xb（pm8150b、pm8009）访问失败 `-5`，暂不影响。

### USB 线路已接通（MAX77705）

- 修复前屏幕诊断：UDC `state=not attached`，`DCTL=0x80f00000`（RUN_STOP=1），`DSTS` 链路状态 3（挂起），
  `GCTL/GSTS` 为设备模式；dwc3 glue 已打开 VBUS 覆写，PHY 也置位了 `VBUSVLDEXT0`；主机侧没有任何
  连接事件。控制器和 PHY 都在工作，D+ 上拉到不了接口。
- Tab S7 的 D+/D− 经过 **MAX77705 的 MUIC 开关**（FSA4480 在厂商 DTB 中 disabled）。厂商
  `max77705_muic_shutdown()` 关机时调用 `com_to_open()`；用 `sysrq-b`（不走关机回调）重启进 6.18
  后仍未接通，说明 ABL 正常启动时也不会把它接到 USB。6.18 需要自己把开关切到 USB。
- 协议（厂商源码）：I2C `i2c@980000`（QUP0 SE0，GPIO 28/29，主线 `&i2c0`），从地址 `0x25`；
  写 `0x21 = [0x06, 0x09]`（`COMMAND_CONTROL1_WRITE`，`COM_USB`）后写 `0x41 = 0x00`；
  读用 opcode `0x05`，结果在 `0x51`；`BC_STATUS`（`0x08`）bit7 为 VBUS。`COM_OPEN = 0x3F`。
- bring-up 配置已补 `I2C`、`I2C_CHARDEV`、`I2C_QCOM_GENI`、`DMADEVICES`、`QCOM_GPI_DMA`，
  DTS 启用 `&i2c0`。initramfs 在绑定 gadget UDC 前执行上述 COM_USB 写入，不改充电寄存器。
- 初次失败的直接原因是 initramfs 的 sysfs 匹配，不是 I2C probe：实际适配器路径为
  `/sys/devices/platform/soc@0/9c0000.geniqup/980000.i2c/i2c-0`，旧模式 `*980000.i2c`
  因末尾还有 `/i2c-0` 而匹配不到。改为 `*980000.i2c*` 后找到 `/dev/i2c-0`；单适配器回退仅作诊断保护。
- 真机证据（候选 `out/boot-shim/muic-usb-adapter-20261024-01/boot.img`，SHA-256 `f4edfbbb…65ce`）：
  主机 `lsusb` 出现 `1d6b:0104`，UDC 为 `configured`；telnet `192.168.77.2` 可执行命令，
  `uname` 为 `6.18.58 aarch64`，`usb0=192.168.77.2/24`。MUIC 读回 `BC_STATUS=0x81`（VBUS=1），
  `0x51..0x52 = 0x06 0x09`，确认开关保持在 COM_USB。

写入通道：TWRP 内用 `scripts/device/flash-via-recovery.py`；运行中的 4.19 SteamOS 内用
`scripts/device/push-boot.py`（支持 `--partition dtbo`）。

## 真机 Game Mode 记录（2026-10-25）

**进程运行记录：6.18.58 冷启动后自动启动 Game Mode 进程；systemd `running`、0 个失败单元，外层 USB/telnet 保持可用。实际显示仍未验收，后续复测确认黑屏，见[显示恢复记录](display-recovery.md)。**
当时的候选为 `out/boot-shim/steamos-mainline-final-20261025-02/boot.img`，SHA-256
`432d5c56c8d7b774e993f1bfbaeb8d5f00602b86bd77d1a641e04bf7a66d1f54`，写入 `/dev/sda22` 后读回一致。

- UFS 的 `PARTNAME=userdata` 找到 `/dev/sda35`，F2FS 双超级块和 UUID 校验通过，持久 SteamOS 可写挂载。
- `SM_GPUCC_8250` 使 Adreno SMMU/GMU 正常工作，`FW_LOADER` 加载 SQE/GMU；厂商 SQE 0x93 换成
  linux-firmware 0x112；zap 保持设备原厂分离 `.mdt + .b00/.b01/.b02`，不拼接、不绕过鉴权。
- `/dev/dri/renderD128` 存在，`vulkaninfo --summary` 报告 `Turnip Adreno (TM) 650`
  （`0x5143:0x6050002`），GMU firmware v2.1.8。
- DSI-1 `connected`，模式 1600x2560@120；系统 gamescope 选中 Turnip、DRM modifiers、
  Wayland/Xwayland 和原生 ARM64 Steam 均实际运行。`tabs7-gamescope-session.service`、
  `upower.service`、`steamos-manager.service` 均 active。
- initramfs 保留 BusyBox PID 1 与 telnet，在独立 PID/mount namespace 里以 chroot 启动
  `graphical.target`。重复 telnet 和不存在的 syspersist 被屏蔽；upower 在此调试 namespace 使用
  `PrivateUsers=no`，timesyncd 清除 `ConditionVirtualization`。不能用 BusyBox `switch_root`，
  它会删除外层 RAM root；pivot_root 在该 rootfs 上也不是可用路径。
- `TRACING/FTRACE/FUNCTION_TRACER` 已启用，steamos-manager 的 tracefs 实例可以创建。
  NTP 显示 `System clock synchronized: yes`；主机 `tabs7linux-usb` 为 `shared`，平板默认路由
  为 `192.168.77.1`，`https://store.steampowered.com` 返回 HTTP 200。
- 临时 `a6xx init`/`mdss init` 阶段日志已全部移除。仓库测试为 `86 passed, 1 skipped`。

未完成的独立验收：触摸、Wi-Fi/蓝牙、Proton fsync、vkd3d-proton sparse、Desktop Mode。
启动期仍见过显示 SMMU fault 和 DSI command timeout，不能把当前结果描述为所有显示问题已解决。

## 按顺序的阻塞项

1. ~~**早期调试通道与 USB 线路**~~（已完成）：simpledrm + fbcon 显示 6.18 日志；
   MAX77705 已切到 USB，主机枚举 `1d6b:0104`，telnet shell 可用，见上文。
2. ~~**initramfs 找到 userdata**~~（已完成）：6.18 真机通过 `uevent PARTNAME` 找到
   `/dev/sda35`，F2FS 双超级块 magic/UUID 和读写挂载已验证。partlabel 分支仍不是本机路径。
3. ~~**显示与 Game Mode**~~（主线后端已验证）：见[面板与双 VIG](panel-6.18.md) 和上面的记录；
   仍需单独清理启动期 SMMU/DSI 错误并验证具体平面分配。
4. ~~**GPU 用户空间**~~（已完成）：系统 freedreno/Turnip(`msm`) 与系统 gamescope 已替代
   `TABS7_ZINK_KGSL=1`、私有 ICD 和 `mesa-zink-gnome-x11` 的旧会话路径。
5. **输入与其他网络**：触摸、Wi-Fi、蓝牙都需要对应的主线驱动与固件路径；USB 共享网络已完成。
6. ~~**BOOT 封装**~~（已完成）：shim + 厂商 DTB + 原 Lineage DTBO/vbmeta，只写 BOOT 并读回校验。

剩余验收：Proton 日志里有 fsync，vkd3d-proton 能识别 sparse；触摸、Wi-Fi/蓝牙和
Desktop Mode 也各有独立的真机验收。4.19 路径（`scripts/build/vendor/`）继续保留作回退。

## 6.18 BOOT 候选

`scripts/build/mainline/build-steamos-6.18-boot.py` 把 6.18 内核打进 BOOT：沿用设备上
正在用的 BOOT 的 header v2 参数与命令行，只替换三个载荷（`Image`、DTB、initramfs），
用同样的 rollback index/location 重新签 AVB。

```sh
scripts/build/mainline/build-steamos-6.18-boot.py \
    --snapshot out/steamos/native-preflash3 \
    --deployed-boot out/steamos/boot-preflight-20261024-145309/boot/boot.img \
    --kernel out/steamos-kernel-6.18/Image \
    --dtb out/steamos-kernel-6.18/sm8250-samsung-gts7lwifi.dtb \
    --ramdisk out/steamos/boot-preflight-20261024-145309/boot/initramfs.cpio.gz \
    --output out/steamos-kernel-6.18/boot
```

写入前它会依次核对：同一次启动的四分区快照（manifest 哈希、PIT 容量、DTBO/RECOVERY
布局、实时 DTB、board-id/msm-id）、已部署 BOOT 的 AVB 签名与 header v2、内核确实是
6.18、DTB 里确实有面板节点，最后核对重打包后只有三个载荷变化、header 与命令行逐项
一致、镜像正好等于 BOOT 分区容量。

当前产物：`out/steamos-kernel-6.18/boot/boot.img`（`f0708f9d…67a5`，71,303,168 字节），
内核 6.18.58，`text_offset=0x80000`、`res3=0xecefecef` 从已部署内核复制，DTB 含面板节点，
AVB footer 已验证。initramfs 与 4.19 上正在运行的那个相同。

脚本只生成镜像，写盘要单独授权。回退镜像是
`out/steamos/boot-preflight-20261024-145309/boot/boot.img`（`37f5aa13…`）。
