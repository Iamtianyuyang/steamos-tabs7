# 显示恢复记录（2026-10-25）

目标是先恢复可见界面。主线 6.18 的 `connected`、模式枚举、GPU 能力与
Game Mode 进程状态，均不足以证明面板实际出图。

## 主线复测

1. 用户反馈停在三星标志或启动警告。进入 TWRP 后，ADB 确认设备为
   `gts7lwifi`，硬件修订 7，verified boot 状态为 `orange`。
2. 当前 BOOT 读回为 `9e2580a9ce762d02561b5437bf89f6a06e2b8bb55628e903e7fc6832f783280f`，
   与 `display-core-reset-preserve-pll-20261025-01` 实验相符。已先备份完整分区与工作区差异。
3. 恢复 6.18 基线 `432d5c56c8d7b774e993f1bfbaeb8d5f00602b86bd77d1a641e04bf7a66d1f54`，
   整个 BOOT 读回一致。USB/telnet 和 SteamOS 进程恢复，用户确认仍黑屏。
4. 内核有旧 splash 地址 `0x9c...` 的 SMMU fault；背光报告 462。DRM 软件状态
   报告 1600×2560@120、两个 mixer，但未证明持续 120 Hz 扫出或可见画面。
5. 单项候选仅在基线面板驱动的 prepare / ESD 路径补独立 display-on 命令，
   不带工作区的 shim、DTS、MDSS、DPU、DSI 调试改动。编译和封装通过，
   BOOT 为 `e5cd44f8316814f4b07abfca13b65c37532de76ac991c6bad7ab16fb40be7547`，
   写入与读回一致；用户仍确认黑屏，因此不能记为显示修复完成。
6. 停止 Steam 后，直接 modetest 的 legacy modeset 成功提交了 framebuffer；
   实际 DRM client、framebuffer 和进程等待状态均已读取。空闲时软件 vblank IRQ
   不增长；后续只读采样发现 INTF1/2 的硬件帧计数仍同步增长，不能据软件
   IRQ 判断硬件停止，也不能仅以 ioctl 返回成功作为出图验收。atomic `-s` 单独调用未建立有效测试，
   不作为显示正常的证据。

## 临时恢复路径

回退 BOOT 是已有的 4.19 镜像：

- 路径：`out/steamos/boot-preflight-20261024-145309/boot/boot.img`。
- SHA-256：`37f5aa13d2d2d64286ab5b3ecc304e4dc9a6eb00ffe508abc60669c4d9741995`。
- 已写 BOOT，完整分区读回一致；DTBO、VBMETA 和用户数据未重刷或清除。
- 内核已验证为 `4.19.325-cip136-st20-perf-g4478511c4b37`，PID 1 为 systemd，
  Game Mode 与 USB 调试服务均 active。用户随后确认“已显示完整 Steam 界面”。
  这次验收仅确认恢复出图，没有重做触摸、键盘或游戏性能验收。

为避免混用 GPU ABI，设备上临时保留两个启动脚本：
`gamescope-session.mainline-6.18` 是回退前脚本的副本；
`gamescope-session.vendor-4.19` 来自提交 `9627a886e` 的厂商内核启动脚本。
入口以 `/dev/kgsl-3d0` 是否为字符设备选择对应路径；此调整仅部署于设备，
尚未替代仓库的主线默认会话。debug-shell 单元恢复并添加同样的设备条件，
原始单元和会话保存在设备 `/var/tmp/display-recovery-20261025/`。

本轮本地证据集中于忽略目录 `out/display-recovery/20261025/`，包括镜像哈希、
回退前分区、工作区 patch、构建日志、模式状态与运行时部署内容。
主线显示的根因尚未锁定；未提交的其他实验没有被合入本轮单项候选。

## 可重复诊断与复位时序（进行中）

- 主线已启用 MODULES / MODULE_UNLOAD / MODVERSIONS / BASIC_MODVERSIONS。
  `allnoconfig` 下必须显式开启 BASIC_MODVERSIONS；否则虽设置 MODVERSIONS，
  外部模块仍缺少 `__versions`。构建脚本产出匹配 Image 的 Module.symvers。
- `hardware/.../kernel/diagnostics/` 的只读快照模块已分别在工作中的 4.19
  和黑屏的 6.18 加载、读取并卸载；检查了运行配置哈希和每项导入符号 CRC。
  PLL 采样基址为 `0xae94900` / `0xae96900`，不能把 lane 区误认成 PLL。
- 主线硬件 INTF1/2 帧计数两次分别为 `0x13f9`、`0x41d5`，都同步增长。
  这些计数证明扫描引擎运行，不证明面板接收或显示了图像。
- 4.19 中 GCC_DISP_XO_CLK 开启、6.18 中关闭。临时 CCF 引用开启该时钟，
  重新关屏、legacy modeset 输出全屏图，用户仍确认黑屏；探针已卸载。
  该差异目前不能作为根因或永久时钟修复。
- 三星亮度路径发送高字节在前，现有主线 callback 使用低字节在前的 helper。
  用 `257`（两字节均为 `01`）对照，用户仍确认黑屏；亮度字节顺序需要修正，
  但不足以解释当前全部问题。此轮复位对照暂不混入亮度修改。
- DCS 状态读取未成功：首次 power read 为 `-110`，后续 ID/亮度读取为 `-22`。
  零值不是有效面板响应，原始结果为 `mainline-dcs-status*.txt`。
- 发现独立的复位极性错误：DTS 的 TLMM82 为 GPIO_ACTIVE_LOW，而驱动直接
  把厂商物理 `<0 12>,<1 12>,<0 12>,<1 12>` 当成逻辑值传给 gpiod。
  实机初始化后 TLMM82 实际为低电平，处于复位状态。已把驱动改成逻辑
  `1,0,1,0`，断电及首次申请保持断言复位；DTS 极性不改。
- 新测试编译并执行实际 power_on/off 函数，用 DTS 极性将 gpiod 逻辑值映射
  为物理值，与独立厂商 profile 的完整时序比较：修改前失败，修改后相关
  17 项测试通过。新 BOOT 候选为
  `63822b114688eb28554db2d4013fcd0b3d6af79a9154123d3beb4a081c5cca48`。
  真机出图验收仍在进行，尚不能记为修复完成。

### 复位修正版的实际结果

修正版 BOOT 全分区写入/读回一致，TWRP 中再次核对同一哈希，DTBO/VBMETA
保持原哈希。6.18.58 #42 已正常执行，TLMM82 为高电平。停止 Game Mode 后，
legacy modetest 持有 DRM master 并提交 1600×2560@120 全屏测试图，亮度设为
`257`；用户仍确认黑屏，因此复位修正是已验证的控制问题修正，不能称为完整显示修复。

修正后 DCS 调用开始返回数据：power `0x0d`、ID `130000`、brightness `3400`；
再次开启 XO 并重做 modeset 后，读值变为 `0x00`、`080808`、`0202`。
这些值尚未通过有效性核对，而且伴随 DSI error worker 状态，不能只凭 API 成功
宣称面板状态正常。对照日志分别保存在 `reset-polarity/` 中。

亮度字节序的独立回归测试执行实际 callback 和内核 DCS helper：修改前输出
`51 ce 01`，与厂商要求 `51 01 ce` 不符；源码现改用 `_large` helper，
本地 80 项测试通过、1 项跳过。当前真机仍是只改复位的候选，亮度修正尚未部署。

### ESD 自触发环与半屏结果

后续日志发现 TLMM66 对应 IRQ164 累计 188 次，恢复计数达到 98 次，几乎每次
初始化完成就再次恢复。TLMM46 对应 IRQ165 为 0；不能把两条线合称为来源。
原主线 handler 不检查 prepared/enabled，恢复时还允许自己的复位边沿重新入队；
init 会写亮度零，恢复路径却未恢复用户亮度。

临时、可卸载的比较模块核对机器 compatible、irq chip 与 hwirq=66 后屏蔽 IRQ164，
不安装自动服务。重新 modeset 并以正确字节序设置亮度后，连续 8 轮原始读回均为：
power `0x9c`、ID `07f241`、brightness `01ce`，中断计数不再增长。
这说明此前变化的读值被不断的恢复过程干扰，不能据此认定 PHY 数据损坏。

用户最初回复“完整显示”，随后明确修正：**横屏上半黑，下半彩色**。因此只记为
从完全黑屏恢复到半屏出图，不记全屏通过。另用 VIG1/VIG2 两个独立 overlay，
分别覆盖原生 x=0..799、800..1599，两个实际 framebuffer/pipe 均已提交；用户仍见
半屏。该对照排除了“只把单一 primary 拆成独立图层即可恢复”的简单结论，
双 DSI 控制初始化与视频链路仍须检查。

正式 ESD 候选忽略初始化、未启用及恢复中的中断；恢复成功后补 display-on、
恢复用户亮度，失败时关闭已启用的供电。回归测试把复位产生的 IRQ 直接送入实际
handler/work：修改前错误入队，修改后不自触发且恢复亮度。81 项通过、1 项跳过。
候选镜像 `59937f2c60f5bee413fa6861c5ef730a076e7def6b260636299195f0f3758f68`
已封装并验证，尚未刷入；真机仍在使用带临时 IRQ 隔离的复位修正版。

模块构建与固定诊断工具已作为 `c23da1796` 提交；未把仍未验收的全屏显示记为完成。

### 第二条 DSI 的独立读回

主线虽启用双 DSI 视频 bonding，驱动实际只向 `ctx->dsi[0]` 发初始化。
DSI1 独立原始读回为 power `0x08`、ID `07f241`、brightness `0000`，仍在休眠。
临时探针只向 DSI1 重放同一份 359 条命令并发送 display-on；其 power 变为
`0x9c`，但用户仍见半屏，brightness 仍读回零。该结果确认第二条链路漏初始化，
也表明运行时补命令不足以完成完整视频输出验收。

源码已把 profile、display-on/off、sleep 和亮度命令显式送往两条链路；
没有启用 `qcom,sync-dual-dsi`，没有修改视频分割和 PHY 时序。新的主机 C 回归
执行真实 init 函数，分别与两条链路应收的完整 profile 比较：修改前 DSI1
收到 0 条，修改后两边均为 359 条；相关总测试 82 项通过、1 项跳过。
新候选 BOOT 为 `9c8f0fcfb4d68cd6cd792f05c042382ae1f4aa8692edef87866846dc82acd42b`，
正在进行写入、重启及无临时隔离模块的对照验收。

### 无临时模块的重启对照

`9c8f0fcf...42b` 已写入，完整读回一致；6.18.58 #44 成功启动，启动时模块列表为空，
TLMM82 为高电平，两个面板中断计数均为 0。完整 DPMS off/on 后，TLMM66 累计
3 次边沿，但没有 ESD work 自循环，说明状态门控在真机上生效。

启动直接读回仍不可靠；完整重新 modeset 后 DSI0/1 状态变为 `0x9c` / `0x9e`，
ID 均为 `07f241`。init 后亮度读零，手动在 ioctl 完成后写回 462，主链路读回
`01ce`。又发现驱动注册的 backlight 没有赋给 `panel.backlight`，DRM 核心因此不会
在 enable 时恢复亮度。源码已补关联，并通过实际 helper 测试校验 blank 时发送零；
这项尚未部署。当前 83 项测试通过、1 项跳过。

在新内核上重新做两个独立 VIG overlay（并等 modetest 到 anon_pipe_read，确保
模式设置完成后再写亮度），用户仍确认一半黑一半彩色。不能把 DSI1 漏初始化当成
半屏问题的完整原因。

正在测试两条 DSI 内部 TPG，以绕过 VIG/显存：临时模块仅复制内核已有的
video test-pattern 设置，保存寄存器并在卸载时恢复；没有写 PLL、供电或时钟。
物理读回两侧均为 INIT=ff、MAIN=100、VIDEO_CFG=5、CTRL=35，原始快照为
`mainline-internal-pattern.*`。用户确认两半均黑，没有得到已知正常半屏上的正向测试结果。随后切换为厂商支持的
固定色 selector=2（两条链路分别红/绿，CTRL=25），用户仍确认两半均黑。
因此这两种内部 TPG 试验不能定位剩余半屏故障。两次试验均保存并恢复原寄存器；
固定色的 60 秒自动卸载已完成，模块列表为空，清理日志在
`out/display-recovery/20261025/panel-dual-ddic/pattern-cleanup.txt`。

### DRM 背光生命周期候选

将注册的 backlight 关联到 `drm_panel.backlight`，并让 callback 使用
`backlight_get_brightness()` 遵守 blank/power 状态。候选 BOOT 为
`a81bb920bbd5ecdb401c37635d29ab60eae025e1cdb16f519cc12c94512ea9fe`，
已写入并完整读回一致，DTBO/VBMETA 哈希未变；6.18.58 #45 重启后进行了完整 DPMS off/on，modetest 到达 `anon_pipe_read`；
未手动写亮度，DSI0 原始读回 power `9c`、ID `07f241`、brightness `01ce`，
用户确认仍半屏彩色。背光模式切换恢复已验证，半屏问题未解决。
原始读回为 `panel-backlight-lifecycle/dcs-status-no-manual-write.txt`，
寄存器快照为 `mainline-backlight-lifecycle.*`。

### 接口时序对照与 PLL 初始化回归

4.19 快照的 INTF2_TIMING_ENGINE_EN 为 0，而 6.18 为 1。临时模块只保存并
清除此寄存器（主 INTF1 仍为 1），用户确认仍为半屏；卸载已将原值 1 恢复，
模块列表为空。该差异不能作为已找到的根因，未进入正式内核。

已验证的面板复位、双链路命令、ESD 门控及背光生命周期修正作为 `6215dbc2e`
提交；提交说明明确完整显示未通过。

另一个独立问题与[上游 PLL 初始化修复提案](https://lkml.iu.edu/2610.2/06878.html)
相符：`dsi_7nm_phy_enable()` 先把 bias 引用计数设为 1，使后续
`dsi_pll_enable_pll_bias()` 提前返回，两个 PHY 的 SYSTEM_MUXES 都未写入。
正常 4.19 实际读回为 `c0`，当前 6.18 为 `0`。主机测试执行实际完整 PHY enable
和 bias helper，以两组独立 MMIO bank 模拟两条链路；修改前 D-PHY/C-PHY
两个用例均在首次启用读零而失败，warm enable 仍正确；补首次写入后均通过。
总测试为 85 项通过、1 项跳过。该项候选 BOOT 为
`25ebef6b9fbb60e9103e95dd11ec86190e401c327a6788466e11bdb23ba3b063`；
仅增加首次 PHY 初始化的 SYSTEM_MUXES 写入，没有改动 PLL 频率、时序、
桌面或输入路径。正在进行写入后重启验证，尚未据此宣称半屏已修复。

### PLL 修正版真机结果：全屏测试图

`25ebef6b...3b063` 已完整写入与读回校验，DTBO/VBMETA 未变，运行内核为
6.18.58 #46。冷启动后的只读采样确认 DSI0/1 SYSTEM_MUXES 均为 `c0`。
完整 DPMS off/on 后，同样的两个独立 800×2560 VIG overlay 已提交，
两条 DSI 均读回 power `9c`、ID `07f241`、brightness `015a`，不再出现
第二条链路亮度恒为零；用户明确确认 **完整显示彩色图**。

因此首次 PHY 初始化漏写 SYSTEM_MUXES 是本轮剩余半屏故障的有效修复；
原始证据在 `pll-first-enable/`、`mainline-pll-first-coldboot.*` 和
`mainline-pll-first-dual-overlay.*`。这次尚未验收 Steam 实际内容、冷启动
自动出图及输入功能。冷启动早期主链路仍曾读到休眠 `08`，需与实际会话
启动阶段对照，不能把手动 DPMS 后的全屏通过写为完整启动验收。

### Steam 渲染器系统调用缺失

切回 Game Mode 后，用户看到持续打印的控制台，而非 Steam。DRM 实际扫描的是
`allocated by = [fbcon]` 的 FB91，Steam 仅创建了辅助小窗口，没有主界面窗口。
不能以 gamescope/steam 进程存在作为实际会话通过。

Steam 的 `steamwebhelper.log` 重复报 page allocator `Function not implemented (38)`；
对 zygote 子进程做 5 秒、只记录 madvise 的限时 strace，实际捕获到
`madvise(..., MADV_DONTNEED) = -1 ENOSYS` 后紧接 `SIGTRAP` 和子进程退出。
独立匿名页 syscall 探针同样返回 rc=-1、errno=38。内核
`CONFIG_ADVISE_SYSCALLS` 未启用，`mm/Makefile` 因此未链接 madvise.o。

仅在 SteamOS 基础配置补 `CONFIG_ADVISE_SYSCALLS=y`，保留所有已验证的面板
修正。新镜像为 `503b3d0d94050e1daa7bcd55bd450cc3023cd7f09d4bee251ed506945991882c`；
配置核验确认保留该项，85 项主机测试通过、1 项跳过。待重启验证 syscall
返回值、渲染器稳定性、实际窗口和用户可见界面。修复前证据为
`pll-first-enable/madvise-trace.txt`、`madvise-syscall-before.txt` 等。

`503b3d0d...1882c` 已完整写入并读回校验，启动内核为 6.18.58 #47。相同匿名页
探针返回 rc=0、errno=0；Steam 的 2560×1600 主窗口已创建，DRM 记录实际
Gamescope FB101，截图包含 Steam 的 Logging in 页面，CEF 不再出现原来的
madvise 崩溃循环。但用户确认物理屏幕黑屏；DCS 仍为休眠 `08`、亮度零，
所以仅确认 Steam 系统调用与界面生成已修复，冷启动面板交接仍未通过。

### 冷启动交接：停止 bootloader 遗留的 INTF

对照 #47 冷启动（73 秒）与手动 DPMS 后的寄存器快照：两者的 DSI_CTRL 均为主线写入的
`0x173`，说明 DSI host 已由主线编程；但冷启动的 DSI0/1 FIFO_STATUS 为 `0x14441019`，
比正常态 `0x10001010` 多出视频 MDP FIFO 溢出/欠载、DLN0 LP FIFO 溢出和三条 HS FIFO
溢出的粘滞位。同一次启动的日志中，1.2–11.2 秒持续出现旧 splash 地址 `0x9c...` 的
SMMU fault（每 5 秒约 80 万条，SID 0x820/0xc20），直到 11.6 秒 fbcon 第一次 modeset。

原因：bootloader 让 INTF1/2 一直扫描 splash，主线 DPU 不接管也不停止它们。第一次
modeset 的 DSI pre_enable 打开视频引擎时，旧时序立即开始推流，面板初始化命令随后
才以 LP 发出，被挤掉，面板仍读回休眠 `08`。手动 DPMS 时 encoder disable 已关闭
INTF，所以同样的命令能被接收。

修正只在 `dpu_kms_hw_init()` 中、RM 建好 INTF 之后、创建 DRM 对象之前，读取每个
INTF 的状态，把仍在运行的时序引擎关掉并等两帧；没有改 DSI、PHY、面板时序或 DTS。
主机测试执行实际 helper：INTF_1/INTF_2 运行时各写一次并等待 40 ms，空闲时不写、不等待，
并检查调用顺序；去掉修正时测试失败。总计 86 项通过、1 项跳过。

候选 BOOT `4aae9eaed3f1488c14bc57d777cf28524e4dd508fc80e5ed47d03d79b2d74db6`
只换内核（`.config` 哈希仍为 `9b677c4a...`，DTB、initramfs 和命令行与 #47 相同）。
从运行中的 6.18 写入 `/dev/sda22`，清缓存后完整读回一致；DTBO `9383ef37...`、
VBMETA `c92f5f6a...` 未变。6.18.58 #49 冷启动结果：

- 1.11 秒记录 `stopping INTF_1/INTF_2 left enabled by the bootloader`；
- 整次启动 SMMU fault 共 10 条，`dsi_err_worker` 为 0，ESD IRQ164 仅 1 次边沿且无恢复；
- 内核执行 `/init` 从 11.9 秒提前到 1.67 秒（原来的 10 秒被 fault 风暴占用）；
- 未做任何手动 DPMS 或 modetest，用户先看到控制台文字，随后确认 **进入 Steam 界面**；
  DRM 扫描的是 `gamescope-xwm` 分配的 FB100。

因此冷启动面板交接通过一次实测。仍待：重复冷启动、触摸与 Steam 内操作、亮度 / 关屏 /
唤醒。原始记录在 `boot-intf-stop/`（`flash-session.txt`、`coldboot-steam.txt`）。
