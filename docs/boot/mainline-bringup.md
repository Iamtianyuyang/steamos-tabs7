# 主线内核 bring-up（SM-T870）

本页记录 2026-10-24 的主线启动实验与结论。目标是把设备从厂商 4.19 内核
（KGSL、双 VIG、无 user namespace）迁移到主线，从而一次性消除 SteamOS 移植
中的所有内核侧欠账。

## 已确认的两个 ABL 门槛

厂商镜像能启动、主线镜像进 Download mode，差异不在打包格式（两者都是合法
arm64 Image，`flags=0x0a`、magic `ARMd` 一致），而在下面两项：

### 1. `text_offset` 必须为 `0x80000`

| 内核 | `text_offset` | ABL 行为 |
| --- | --- | --- |
| 厂商 4.19 | `0x80000` | 启动 |
| 主线 6.2（原样） | `0` | 落入 Download mode |
| 主线 6.2（改为 `0x80000`） | `0x80000` | **放行，开始执行** |

主线 Image 是位置无关的（header flags bit 3），修改 `text_offset` 只影响加载
地址，不改代码。补丁位置：Image 偏移 `0x08`，8 字节小端。

### 2. 基础 DTB 的 `qcom,board-id` 必须是 `0 0`

| 基础 DTB | board-id | ABL 行为 |
| --- | --- | --- |
| 厂商 DTB（可用镜像内） | `0 0` | 正常，由 DTBO 表选择 overlay |
| 主线 DTB（仓库内） | `8 7` | 落入 Download mode |
| 主线 DTB（改为 `0 0`） | `0 0` | **放行** |

ABL 通过 **DTBO 表**里的 overlay 选择硬件版本（本机 revision 7 对应表内槽位
6），基础 DTB 自身不应声明最终 board-id。`scripts/recovery/build-recovery-draft.py`
已经会把该槽位替换为 no-op overlay（`out/bringup/recovery-noop-dtbo.img`）。

## 当前状态

- 主线 6.2 + 主线 DTB（board-id 0 0）+ no-op DTBO 表：**ABL 放行，内核开始
  执行，但停在 Samsung 标志**，USB 不枚举（未到 dwc3 gadget 初始化）。
- 说明失败点在早期平台初始化（RPMH / SCM / SMMU / interconnect 一类），
  需要内核日志才能定位。

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

## 复现命令

```sh
# 主线内核（含 text_offset 修正）
python3 scripts/recovery/build-recovery-mainline-vendordtb.py \
  --kernel out/bringup/Image-textoffset-80000 \
  --dtb /tmp/opencode/gts7-dtb-board0.dtb \
  --dtbo out/bringup/recovery-noop-dtbo.img \
  --cmdline "fw_devlink=permissive clk_ignore_unused pd_ignore_unused \
loglevel=8 ignore_loglevel initcall_debug panic=0 rdinit=/init printk.devkmsg=on" \
  --partition RECOVERY --output out/bringup/recovery-mainline-board0.img
```

## 待办

1. 进入 Recovery 读出 `pstore`，定位主线挂住的具体驱动
2. 每次实验只在 RECOVERY 分区进行，避免 BOOT 的不确定行为
3. 确认可用按键：强制复位为「音量下 + 电源」10–20 秒；Recovery 入口需要在
   复位瞬间保持「音量上」
