# 主线 6.18 触摸（NT36523 TDDI）

触摸是 NT36523 显示驱动芯片的触控部分，挂在 QUP0 SE5（`i2c5`，GPIO12/13），
中断为 TLMM15 下降沿。厂商节点在 `input_gts7l_common.dtsi`（`touchscreen@62`，
`novatek,irq-gpio = <&tlmm 15 0x2002>`，`novatek,resolution = <1600 2560>`）。
厂商 4.19 驱动从固件地址 0x01 的寄存器 0 读 6 字节一条的触点记录；0x62 是
bootloader / 硬件地址。

## 实现

- **驱动**：沿用主线 `novatek-nvt-ts`。触点格式（`id<<3 | status`、x/y 各 12 位）
  与厂商一致。新增 `novatek,nt36523-ts`；DT 带 `panel` 时作为 `drm_panel_follower`
  工作，不申请 reset 和供电（两者属于面板），只在面板 prepared 且输入设备打开时
  使能中断。几何参数取自 DT，因为面板上电前无法读取芯片参数。状态 5（手掌）
  以 `MT_TOOL_PALM` 上报，6（手套）按手指处理；未上报的槽位由 `INPUT_MT_DROP_UNUSED` 抬起。
- **DT**：`i2c5` 启用，`touchscreen@1`、`panel = <&display_panel>`、1600×2560。
- **GPI**：固件把 SE5 设为 GSI 模式，i2c5 必须走 GPI DMA。主线 sm8250 的 EE 偏移为 0 时，
  gpi_dma0 每次 `EV ALLOCATE` 都超时，`geni_i2c 994000.i2c` 以 `-19 Failed to get rx DMA ch`
  失败（#50）。厂商 kona.dtsi 给 gpi_dma0 的 `qcom,gpi-ee-offset` 为 `0x1000`；gpi 驱动现在
  读取这个可选属性，本板只给 gpi_dma0 设置。gpi_dma1/2 的厂商值为 `0x6000`，目前没有
  GSI 客户端，未设置也未验证。
- **配置**：`steamos-display-6.18.config` 增加 `INPUT_TOUCHSCREEN`、
  `TOUCHSCREEN_NOVATEK_NVT_TS`；与 #49 相比 `.config` 只差这两项。

主机测试（`tests/steamos/test_touch_nt36523.py`）执行实际的中断解码与面板跟随函数：
厂商格式的触点、手掌、手套，以及“先打开再上电、面板下电、关闭期间上电”的中断平衡；
同时核对 DT 与厂商节点、gpi_dma0 偏移与厂商值。去掉驱动修改时测试失败；全部 89 项通过、1 项跳过。

## 真机记录（2026-10-25）

BOOT `f3ef5daa676fd9c73698ae1e57967969fdb3ab0a0069103dd3660c9ac6d71f7a`
从运行中的 6.18 写入，完整读回一致，DTBO / VBMETA 未变；内核 6.18.58 #51。

- `input: nt36523-ts ... 994000.i2c/i2c-5/5-0001/input/input4`，gamescope 打开 event2；
- `i2ctransfer` 读 0x01：FWINFO `24 db 20 32 06 40 0a 00 0e 0a 00 01 02 01 08 10`
  （固件 0x24，校验字节互补，1600×2560，10 点，中断类型 1=下降沿），RESET_COMPLETE `0xa3`；
- 用户在 Steam 界面点击和滑动，确认 **Steam 有反应**；TLMM15 中断 226 次，无 I2C 错误。

### 会话触摸模式

主线会话最初没有传 `--default-touch-mode`，gamescope 使用默认的 1（left）：每个触点
被翻成鼠标左键，滑动变成拖拽，列表不能滚动；用户反馈“逻辑不适合触屏”。
`userspace/steamos/gamescope-session` 改为与 SteamOS 会话相同的
`--default-touch-mode 4 --hide-cursor-delay 3000`（4 = passthrough，原生触摸交给 Steam）。
部署到设备的 `gamescope-session.mainline-6.18`（SHA-256 `93bd4ec4...ec39`，与仓库一致）后
以 SIGKILL 结束旧 gamescope，由单元的 `Restart=on-failure` 重新拉起；新进程命令行含
该参数。用户确认单击触发、滑动可滚动，触屏逻辑 **正常**；TLMM15 中断累计 904 次。

原始记录在 `out/display-recovery/20261025/touch2/`（含 `touch-mode-passthrough.txt`）。仍待：多指、四角与边缘坐标、
Steam 内长时间操作、DPMS 关 / 开及锁屏后的触摸恢复、屏幕键盘与输入。
