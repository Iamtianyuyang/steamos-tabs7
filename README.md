# Samsung Galaxy Tab S7 的 SteamOS 移植

目标设备：**SM-T870 / gts7lwifi**，硬件版本 7。目标是把 Valve 的 **SteamOS ARM
用户空间（Game Mode + Desktop Mode）完整跑在平板上**，内核目标是**主线 6.2**
（`linux-sm8250-v6.2/`，双 VIG 显示方案），rootfs 使用 Valve 官方镜像。

当前状态：SteamOS rootfs 已在真机解出并校验，Steam 客户端已安装、**大屏登录页
已在平板上显示**，私有 Turnip 已在 Adreno 650 上全屏绘制 Vulkan 场景，Steam 内
触摸（Xwayland 直连）已实测可用，原生 initramfs 已固定切到 SteamOS 树。
**独立 Game Mode（gamescope 自持 KMS）、Steam Input 设备节点与游戏启动仍在
适配**。完整进度、边界与证据见
[SteamOS 移植文档](docs/steamos/README.md)。

GNOME/Phosh/XFCE 桌面线已从仓库移除。**主线 6.2 至今没有真机启动记录**：
目前所有真机结果都是在厂商 Lineage/Samsung 4.19 内核上取得的，这条路径暂时
保留到 6.2 达到同样状态为止。

## 已确认的设备信息

2026-10-13 通过 ADB 读取：Android 13，原厂内核 4.19.113，
Bootloader `T870XXS8DXH1`，硬件版本 `ro.boot.revision=7`，
`ro.boot.flash.locked=0`、`ro.boot.vbmeta.device_state=unlocked`。

下游 `kona-sec-gts7lwifi-eur-overlay-r07.dts` 使用 `qcom,board-id = <8 7>`；
`kona-v2.1.dtsi` 使用 `qcom,msm-id = <356 0x20001>`。这组选择参数已从官方
Recovery 启动后的实时 FDT 验证，`dtbo_idx=6`。

下游 `kona-sec-memory-common.dtsi` 定义 ramoops 为 `0x9fa00000`、1 MiB，
四个日志分区各 256 KiB。真机设备树目录中也存在 `ramoops@9FA00000`。

## 分层

源码、构建入口、设备资料与文档的位置见[项目目录导航](docs/development/project-layout.md)。
要点：

- `userspace/steamos/` 是 SteamOS 会话本体（systemd 单元、gamescope/Sway 入口、
  私有 Mesa/gamescope 补丁）。
- `userspace/platform/` 是会话语义无关的宿主服务与配方（蓝牙、Wi-Fi/CNSS、
  Turnip/Zink 构建、KGSL 规则、双 VIG wlroots 补丁）。
- `scripts/steamos/` 抓取并盘点官方 rootfs、安装 Steam 客户端与 systemd 适配、
  核验 IPC 内核。
- 内核仍是厂商 4.19：`scripts/build/vendor/`、`scripts/build/bluetooth/`、
  `hardware/samsung/gts7lwifi/kernel/`。

## SteamOS rootfs

Valve 的 Steam Frame 镜像以 RAUC bundle 发布，包内是 casync 索引，文件系统由
zstd 压缩的内容寻址分块组成（Btrfs，非 ext4）。脚本固定 build、版本、清单哈希
与整盘哈希，中断后重跑只补齐缺失分块：

```sh
python3 scripts/steamos/fetch-holo-rootfs.py --output out/steamos/rootfs.img
python3 scripts/steamos/inspect-holo-rootfs.py out/steamos/rootfs.img
```

只读盘点不挂载、不执行镜像内程序；镜像、分块缓存与证据都在 ignored `out/steamos/`，
仓库不保存镜像。

## 内核

目标内核是**主线 6.2**：`linux-sm8250-v6.2/`，DTS 入口
`arch/arm64/boot/dts/qcom/sm8250-samsung-gts7lwifi.dts`。工作区路径含空格，
Kbuild 不能就地构建，脚本会同步到缓存目录：

```sh
scripts/setup/prepare-tools.sh
scripts/build/mainline/build-initramfs.sh
scripts/build/mainline/build-bringup.sh image
python3 scripts/build/mainline/verify-artifacts.py
```

产物在 `out/bringup/`：`Image`、`Image.gz`、`sm8250-samsung-gts7lwifi.dtb`、
`initramfs.cpio.gz`、`kernel.config`。默认缓存根是
`~/.cache/tabs7linux/<项目路径哈希>/`，可用 `TAB_S7_BUILD_ROOT` 指定不含空格或
冒号的工作目录。**这组 6.2 产物尚未通过真机启动验证**，不能当作可启动内核。

真机已验证的启动链目前仍是官方 Lineage/Samsung 4.19 内核、DTB 和 recovery
DTBO，由 `scripts/build/vendor/build-vendor-linux.py` 构建 BusyBox 控制环境：
PID 1 为 BusyBox `/init`，根文件系统在 RAM，USB RNDIS/ACM 与 telnet 可用，
RECOVERY 全分区读回与镜像一致。证据见
[原生启动测试记录](docs/boot/native-boot-tests.md) 与 ignored
`out/device/native-vendor-linux/`。

SteamOS 需要的内核变体已在这条 4.19 链上逐项部署：HCI socket 恢复、SYSVIPC
（`semget()` 曾返回 `ENOSYS`）、无 DEVTMPFS 下的热插拔节点服务。内核配置在
`hardware/samsung/gts7lwifi/kernel/configs/steamos-*.config`，核验脚本为
`scripts/steamos/verify-ipc-kernel.py`。切到 6.2 后这些配置项要按主线 Kconfig
重新映射。

写 BOOT 前用一条命令串起全部离线预检（分区快照、布局、身份、AVB、initramfs 逐项比对、
只换 ramdisk 的重建）：

```sh
python3 scripts/build/vendor/preflight-steamos-boot.py \
    --deployed-boot out/steamos/steamos-selector-boot/boot.img \
    --output out/steamos/boot-preflight-$(date +%Y%m%d-%H%M%S)
```

它不接触设备；产物（新的 `boot.img`、`preflight.json`、回滚用的 `backup-view/`）都在
ignored `out/`，写盘与读回比对仍需单独授权。

`boot/initramfs/start-persistent-gui` 校验 userdata 分区（PARTNAME/F2FS UUID）后，
校验 `tabs7linux/steamos` 树（`os-release`、适配标记、systemd），bind 到 `/newroot`、
挂上 devtmpfs，再 `switch_root` 到 SteamOS systemd。**启动目标不再可选**：没有
Ubuntu 出口，也不需要 `boot-target` 文件。校验失败时停在前台 RAM 环境，USB RNDIS/
ACM 与 telnet shell 仍可用，日志在 `/run/steamos-bootstrap.log`。

## 设备备份与写入前检查

```sh
python3 scripts/device/collect-device.py
python3 scripts/device/collect-device.py --backup --output out/device/lineage-recovery
python3 scripts/recovery/recovery-preflight.py --device --pit
python3 scripts/recovery/verify-boot-prerequisites.py
```

`--backup` 需要 root ADB，只读 `boot`、`dtbo`、`recovery`、`vbmeta`，保存镜像、
大小与 SHA-256，从不刷写分区。原厂 Android shell UID 2000 受 SELinux 限制，
读不到 `/proc/cmdline`、原始 FDT 与属性内容，报告会记为读取失败；改用支持 root
ADB 的 Recovery 采集，并把不同启动环境的快照用 `--output` 分开。

`scripts/device/inspect-firmware.py` 从匹配的原厂 AP 包提取 boot/recovery/dtbo/
vbmeta，核对组件哈希、DTB 选择与固定保留区（`hardware/samsung/gts7lwifi/profiles/`）。
原厂固定区与实时固定区共 30 个互不重叠的保留区；实时 FDT 补入的 20 MiB UH heap
与 80 KiB XBL UEFI log 也已在其中。原厂 overlay 对主线 DTB 应用会报
`FDT_ERR_NOTFOUND`，恢复草稿因此使用空操作 overlay 并保留 7 个 DTBO 表项。

获取 root Recovery 的步骤与恢复资源见 [Recovery 安装准备](docs/boot/recovery-bootstrap.md)。
写入任何项目构建的 boot/recovery 镜像前，必须核对：已有分区备份、boot 镜像布局、
实时 DTB 保留区、原厂 DTBO 处理方式，以及解锁状态。

## 首次启动的观察点

初始化脚本会向 `/dev/kmsg` 写入 `Tab S7 Linux bring-up initramfs reached /init`。
USB ECM 使用平板地址 `192.168.77.2/24`，主机接口配置为 `192.168.77.1/24` 后
可用 `telnet 192.168.77.2` 获取调试 shell；USB ACM 同时提供串口 shell。该环境为
开发镜像，USB 调试 shell 不设置登录认证。主机侧 NetworkManager 连接名为
`tabs7linux-usb`（静态地址、禁止默认路由），移除用
`nmcli connection delete tabs7linux-usb`。

## 验证状态

详细清单与证据路径见 [SteamOS 移植文档](docs/steamos/README.md)。

- 已通过：官方 SteamOS rootfs 抓取、整盘 SHA-256、Btrfs 只读盘点。
- 已通过：Steam ARM64 客户端校验（大小、哈希、ZIP CRC、ARM64 ELF）并安装到设备。
- 已通过：Steam 大屏登录页在平板显示；触摸点击、输入框与内层屏幕键盘实测可用。
- 已通过：私有 Turnip 在 Adreno 650 全屏绘制 Vulkan 场景（截图证据）。
- 已通过：SYSVIPC 内核变体写入、读回哈希、重启后 GNOME/GPU/Wi-Fi/蓝牙恢复。
- 已通过：厂商 4.19 内核上的 BusyBox PID 1、USB RNDIS/ACM、telnet 与分区读回。
- 已通过：双 VIG 完整画面（两个 VIG 各绘 800 像素宽左右半屏）与触摸映射。
- 尚未完成：独立 Game Mode（gamescope 自持 KMS）、Steam Input 虚拟手柄节点、
  游戏启动与性能验收。
- 尚未验证：主线 6.2 真机启动（目标内核，构建入口已就绪，无启动记录）。
