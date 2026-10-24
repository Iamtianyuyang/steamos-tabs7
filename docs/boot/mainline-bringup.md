# 主线内核 bring-up（SM-T870）

本页记录主线镜像要通过三星 ABL 必须满足的条件。这些结论是 2026-10-24 用早先的
主线树实测的，属于启动链（ABL/分区）层面，与内核版本无关，主线 6.18 首启直接沿用。
6.18 的构建、BOOT 候选与阻塞项见 [内核迁移](../steamos/kernel-6.18.md)。

## 已确认的两个 ABL 门槛

厂商镜像能启动、主线镜像进 Download mode，差异不在打包格式（两者都是合法
arm64 Image，`flags=0x0a`、magic `ARMd` 一致），而在下面两项：

### 1. `text_offset` 必须为 `0x80000`

| 内核 | `text_offset` | ABL 行为 |
| --- | --- | --- |
| 厂商 4.19 | `0x80000` | 启动 |
| 主线（原样） | `0` | 落入 Download mode |
| 主线（改为 `0x80000`） | `0x80000` | **放行，开始执行** |

主线 Image 是位置无关的（header flags bit 3），修改 `text_offset` 只影响加载
地址，不改代码。补丁位置：Image 偏移 `0x08`，8 字节小端。6.18.58 的原始 Image 同样是
`text_offset=0`；`build-steamos-6.18-boot.py` 从已部署的 4.19 BOOT 复制 `0x80000`。

### 2. 基础 DTB 的 `qcom,board-id` 必须是 `0 0`

| 基础 DTB | board-id | ABL 行为 |
| --- | --- | --- |
| 厂商 DTB（可用镜像内） | `0 0` | 正常，由 DTBO 表选择 overlay |
| 主线 DTB（仓库内） | `8 7` | 落入 Download mode |
| 主线 DTB（改为 `0 0`） | `0 0` | **放行** |

ABL 通过 **DTBO 表**里的 overlay 选择硬件版本（本机 revision 7 对应表内槽位
6），基础 DTB 自身不应声明最终 board-id。`scripts/recovery/build-recovery-draft.py`
已经会把该槽位替换为 no-op overlay（`out/bringup/recovery-noop-dtbo.img`）。

## 与其他记录的矛盾（6.18 首启前要查清）

- 这里记录 board-id 必须是 `0 0`；而 `scripts/recovery/build-recovery-draft.py` 要求 overlay
  应用后是 `8 7`，`sm8250-samsung-gts7lwifi.dts` 现在写的也是 `<8 7>`。
- 早先主线内核按上面两项修正后，ABL 放行，但停在 Samsung 标志、USB 不枚举，说明失败点在
  早期平台初始化，需要日志才能定位。这个结果要在 6.18 上重测。

## 分区行为差异

| 分区 | 容量 | 实测 |
| --- | --- | --- |
| RECOVERY | 86,888,448 | 厂商内核（55 MB）与主线内核（6 MB）都能启动 |
| BOOT | 71,303,168 | 主线内核（6 MB）可启动；厂商内核（55 MB、gzip 后 24 MB）**均回落到 Download mode** |

BOOT 的失败原因尚未确定（镜像头 `image_size` 已试改为 64 MB，未验证成功）。
在查明之前，**不要再用 BOOT 分区做实验**。

## 观测手段

内核 `console=null`（厂商 cmdline），且本机没有可用的调试串口，所以唯一的
日志通道是 **ramoops/pstore**：

- 主线 DTS 与厂商 `kona-sec-memory-common.dtsi` 使用同一保留区：`0x9fa00000`，1 MiB
- 记录区各 256 KiB（record/console/ftrace/pmsg）
- 读取方式：启动厂商内核（RECOVERY 分区），`mount -t pstore` 后查看
  `/sys/fs/pstore/`；原生 initramfs 已提前挂载且不清理

主线启动参数包含 `loglevel=8 ignore_loglevel initcall_debug panic=0`，
若内核运行到 ramoops 初始化之后，`console` 区会保存完整启动日志。

## 待办

1. 6.18 首启：先打通早期调试通道（simple-framebuffer），再读 `pstore` 定位挂住的驱动
2. 每次实验只在 RECOVERY 分区进行，避免 BOOT 的不确定行为
3. 确认可用按键：强制复位为「音量下 + 电源」10–20 秒；Recovery 入口需要在
   复位瞬间保持「音量上」
