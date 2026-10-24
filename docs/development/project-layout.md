# 项目目录导航

仓库只为**在 SM-T870 上完整移植 SteamOS** 服务。GNOME/Phosh/XFCE 桌面线和早先的
主线 6.2 树已移除，目标内核是 `linux-sm8250-v6.18/`。
以下命令从 `steamos-tabs7/` 执行。

| 位置 | 内容与入口 |
| --- | --- |
| `docs/steamos/` | SteamOS 移植总览、进度与验收边界；`gpu-stack.md`、`wlroots-split.md`、`wlroots-render-sync.md` 是显示与 GPU 路径说明 |
| `docs/boot/` | 原生启动与 Recovery 记录；`mainline-bringup.md` 记录主线镜像的 ABL 启动门槛 |
| `docs/hardware/` | Wi-Fi/CNSS 适配记录；`mainline-port-checklist.md` 是主线 DTS 移植清单 |
| `hardware/samsung/gts7lwifi/devicetree/` | `drafts/` 保存历史草稿，`vendor/` 保存下游参考，`overlays/` 保存启动 overlay |
| `hardware/samsung/gts7lwifi/kernel/` | 厂商 4.19 补丁与配置；`configs/steamos-ipc.config`、`configs/steamos-systemd.config` 是 SteamOS 内核变体 |
| `hardware/samsung/gts7lwifi/profiles/` | 设备身份、固件布局、保留区和蓝牙 ABI 的核对资料 |
| `hardware/samsung/gts7lwifi/panel/` | 从厂商源码提取的面板权威参数（时序、链路、初始化序列、PHY 调优） |
| `boot/initramfs/` | 下游原生启动的 `/init`；`start-persistent-gui` 校验 userdata 与 SteamOS 树并把根切到 `tabs7linux/steamos`（无 Ubuntu 出口） |
| `userspace/steamos/` | SteamOS 会话入口（`session.sh`、`gamescope-session`、`steam-client.sh`、`sway.conf`）、`systemd/` 单元与 `patches/`（gamescope 双 VIG、Mesa KGSL WSI） |
| `userspace/platform/` | 会话共用的宿主服务与配方：`bluetooth/`、`network/`、`gpu/`（Turnip/Zink 构建与 KGSL 规则）、背光、runtime、wlroots 双 VIG 补丁 |
| `userspace/services/` | 独立于会话的网络校时配置与服务 |
| `userspace/native/` | 原生 C helper：`display/`（DRM 扫描输出对照）、`gpu/`（Vulkan/EGL smoke） |
| `scripts/build/steamos/` | 在 SteamOS 树内编译 gamescope 与 Turnip WSI |
| `scripts/build/vendor/` | 厂商内核镜像、SteamOS initramfs、BOOT 更新与写盘前预检（`preflight-steamos-boot.py`） |
| `scripts/build/bluetooth/` | HCI 内核变体构建与核验 |
| `scripts/steamos/` | rootfs 抓取/盘点、Steam 客户端与 systemd 适配安装、IPC 内核核验、面板参数提取（`extract-panel-profile.py`）与初始化序列生成（`emit-panel-seq.py`） |
| `scripts/build/mainline/` | 主线 6.18 内核/DTB/initramfs 构建、SteamOS 与显示配置层（`build-steamos-6.18.sh`）、6.18 BOOT 候选（`build-steamos-6.18-boot.py`）与产物核验 |
| `scripts/device/` | 只读设备采集、原厂固件分析、双系统镜像整备（`prepare-dual-boot.py`）与有界设备轮询（`wait-for-device.py`） |
| `scripts/recovery/` | Recovery 封装、AVB 工具调用与写入前核验 |
| `scripts/setup/` | 主机工具准备与下载校验 |
| `tools/device/` | 独立设备分析工具；`read-super-lp.py` 只分析已复制的普通文件；`switch-boot.sh` 在 recovery 内切换 Android/Linux |
| `tests/steamos/` | rootfs 盘点、客户端安装、systemd 适配、内核配置与蓝牙变体回归 |
| `tests/hardware/bluetooth/` | 蓝牙 rfkill 与休眠时钟交接测试 |
| `linux-sm8250-v6.18/` | 目标内核（主线 6.18.58 LTS）：DTS 入口 `arch/arm64/boot/dts/qcom/sm8250-samsung-gts7lwifi.dts`，构建脚本在 `scripts/build/mainline/` |
| `../downstream-sm8250/` | 独立的下游参考 Git 仓库，位于移植目录之外的仓库根，保持独立 Git 身份 |
| `out/` | 忽略的下载、构建产物、设备备份和实机证据 |

常用入口：

```sh
# 目标内核（主线 6.18）
scripts/setup/prepare-tools.sh
scripts/build/mainline/build-initramfs.sh
scripts/build/mainline/build-bringup.sh image
python3 scripts/build/mainline/verify-artifacts.py

# SteamOS rootfs（固定 build 与哈希）与只读盘点
python3 scripts/steamos/fetch-holo-rootfs.py --output out/steamos/rootfs.img
python3 scripts/steamos/inspect-holo-rootfs.py out/steamos/rootfs.img

# SteamOS 内核变体（SYSVIPC 等）
python3 scripts/build/bluetooth/build-bluetooth.py

# 原生 BusyBox 控制环境
python3 scripts/build/vendor/build-vendor-linux.py
python3 scripts/build/vendor/build-steamos-initramfs.py

# 设备事实与原厂固件核对
python3 scripts/device/collect-device.py
python3 scripts/device/inspect-firmware.py --ap '/path/to/AP_T870XXS8DXH1.tar.md5'
```

安装位置与仓库位置分别维护：设备上仍使用 `/usr/local/lib/tabs7linux/`、
`/etc/tabs7linux/` 和原有的 `/run/` 状态目录。目录整理不部署文件、不刷写分区。

历史证据保留原来的位置与哈希（`out/steamos/`、`out/device/`、`out/bluetooth/`
等）；已删除的 `out/gui/`、`out/phosh/`、`out/lockscreen/`、`out/bringup/` 只
作为文档中的历史路径出现，可由对应脚本重建。
