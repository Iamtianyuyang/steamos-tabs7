# SteamOS ARM 移植（Tab S7）

目标：在 SM-T870 上运行 Valve 的 SteamOS ARM 用户空间（Game Mode + Desktop
Mode），内核目标是**主线 6.2**（`linux-sm8250-v6.2/`），显示沿用双 VIG 方案。

> **内核现状**：所有已记录的真机结果都取得于 Lineage/Samsung 4.19 内核；
> 6.2 尚未在真机启动。迁移的分层、配置映射与阻塞项见
> [内核迁移：4.19 → 6.2](kernel-6.2.md)。下面的进度条目按当时的 4.19 环境如实记录，切到 6.2 后
> 需要逐项重做验收（SYSVIPC、HCI、无 DEVTMPFS 的节点服务、KGSL/Turnip 与
> 双 VIG 扫出）。

这份文档记录分层方式、rootfs 来源和验收边界。SteamOS ARM 用户空间和
Steam 大屏登录页已在真机运行；独立 Game Mode、Gamescope 输入与游戏仍在适配。
这与替换 PID 1、完成独立系统启动是不同的验收项。

## 不能做什么

社区的 [SteamOS-ARM-Port](https://github.com/hashtagbasit/SteamOS-ARM-Port) 是当前
最接近的成品：共享 rootfs + 每 SoC 一个内核，装机流程是刷 ROCKNIX ABL，由 ABL
从 FAT 分区读取 `KERNEL`。它支持 SM8250（Retroid Pocket 5 等），但：

- 它替换的是高通参考 ABL；三星的启动链不是同一套，Tab S7 没有对应 DTBO/内核。
- 它的内核是 ROCKNIX 主线树 + 设备 DTB，与本项目已验证的 4.19 厂商内核无关。
- 它的显示方案是 gamescope `--backend drm` 直接驱动 DPU；本项目的面板需要双 VIG
  拆分，见下。

因此**不刷**它的镜像，只借鉴它的用户空间组装方式。官方 rootfs 是同一份。

## 官方 rootfs 来源

Valve 的 Steam Frame 镜像以 RAUC bundle 发布，包内是 casync 索引，文件系统由
zstd 压缩的内容寻址分块组成：

| 项目 | 值 |
| --- | --- |
| build | `20261002.6232440` |
| 版本 | `0.5.3` |
| bundle | `deckard-20261002.6232440-0.5.3.raucb`（约 2.2 MB） |
| 根镜像 | `rootfs.img`，10 GiB 稀疏，SHA-256 `63950ce7f4654be7ad8b3060bbef63579a2758308ed14ed5d130d154ea98a8de` |
| 实际数据 | 87,920 个分块引用 / 62,893 个去重分块，约 4.09 GiB |
| 文件系统 | Btrfs（不是 ext4） |

```sh
python3 scripts/steamos/fetch-holo-rootfs.py --output out/steamos/rootfs.img
```

脚本固定 build、版本、清单哈希与镜像哈希，分块缓存在 `out/steamos/chunks/`，
中断后重跑只补齐缺失分块，最后校验整盘 SHA-256。仓库不保存镜像。

只读盘点无需挂载或执行镜像中的程序：

```sh
python3 scripts/steamos/inspect-holo-rootfs.py out/steamos/rootfs.img
# btrfs-progs 不在 PATH 时可传 --btrfs-tool /path/to/btrfs
```

工具按文件系统魔数选择 Btrfs 或 ext4 后端。Btrfs 使用 `btrfs restore` 只提取
盘点需要的普通文件与目录，不还原符号链接、权限或所有者；该临时子集不可用于
安装。原镜像不写入。不支持的文件系统、读不到 `os-release` 会报错退出，不能把
工具读取失败解释成系统没有安装软件。

## 本轮进度（2026-10-24）

- **已验证镜像**：本地 10 GiB 镜像完整 SHA-256 与上述固定值一致；实际
  `os-release` 为 SteamOS `0.5.3`、build `20261002.6232440`、variant `vr`。
- **已完成主机执行检查**：使用 QEMU user-mode 与已解出的 ARM64 库，Bash
  `5.3.3`、gamescope `3.16.30` 的 `--version` 均返回 0；glibc 为 `2.39`。
  这不验证 Tab S7 的 4.19 系统调用、GPU 或显示后端，也不等同于 S1 原生验收。
- **工具回归**：真实 Btrfs、ext4 小镜像均读到预设系统、用户、包描述和 glibc；
  读取前后的镜像哈希相同。无效镜像和缺少系统身份信息会失败退出，4 项测试通过。
- **客户端材料**：从 Valve 的 `steam_client_publicbeta_linuxarm64` 清单下载
  ARM64 客户端，110,560,587 字节，SHA-256
  `9b5b14d03db3187f1c19119593d793ce22cc7749b120266fdb5bb0fc1f2ee08c`。
  大小、哈希、ZIP CRC 和 ARM64 ELF 检查通过；客户端、CEF 与 SDL 已安装到设备。
- **原生环境已恢复**：使用同一镜像做工具对照，samloader 写入并直接启动后，
  BusyBox PID 1、USB RNDIS、root telnet 均恢复；完整 RECOVERY 读回 SHA-256 为
  `b2f2da4afd82ac2cf3313dd1e09012a5b48a5fff221e54389979d64cea51b4ee`。
  原厂 BOOT、DTBO、VBMETA 实时读回与已有可信备份一致，没有清除 userdata。
- **原生用户空间检查**：SteamOS 的 gamescope `3.16.30`、glibc `2.39` 可在本机
  4.19 内核运行；这属于 chroot 内原生程序执行，尚未切换成独立 SteamOS 会话。
- **GPU 检查**：SteamOS 私有 Turnip 驱动已在 Adreno 650 上全屏绘制 Vulkan
  立方体，截图为 `out/steamos/cube-private-final-screen.jpg`；未设置软件 WSI
  调试标志。原 GNOME 驱动没有替换。
- **IPC 内核已部署**：Steam 的 `semget()` 曾返回 `ENOSYS`；已基于原 4.19
  HCI 修复内核启用 SYSVIPC、SYSVIPC_COMPAT 与 SYSVIPC_SYSCTL，保持 IPC namespace
  关闭。新配置 SHA-256 为 `7b6104ec713df7192b62f3fccbbd955aa4d7c575985179906c72dcfbbb069916`。
  新 RECOVERY 完整读回哈希为
  `7b5ec545a21855569b0bded2f909cd658cd2c6f07b8de7f8f237e062c2a02f1d`，
  已成功重新启动，GNOME/GPU、Wi-Fi 与蓝牙服务恢复。
- **Steam 大屏页已显示**：官方更新器完成约 639 MiB 安装，实际客户端 build 为
  `1792618285`（steamdeck_stable linuxarm64）。已截图确认完整登录页。没有登录
  账号，没有验证游戏。
- **触摸已在 Steam 内生效（Xwayland 直连，非 gamescope）**：早先“触摸无反应”时
  嵌套 GNOME 仍在同一 Xwayland 上，`gnome-xi-stage-owner` 让 Mutter 的主指针
  `XIGrabDevice` 带上 TouchBegin/Update/End，所有触摸序列归 Mutter。停止 GNOME
  客户端、只留 Phoc 与 Steam 后，用复制 `sec_touchscreen` 轴范围的 uinput 虚拟
  触摸屏实测：点击“Create account”、“Sign in”、输入框均生效，Steam 自带屏幕键盘
  弹出，Backspace 与字母键逐个准确输入（截图 `out/steamos/touch-*.jpg`）。
  坐标关系：屏幕 X ≈ 原始 y，屏幕 Y = 1599 − 原始 x。真人手指尚待复核。
- **热插拔设备节点**：厂商内核没有 DEVTMPFS，`/linux/dev` 是普通 tmpfs，
  systemd-udevd 不建节点，新的 `/dev/input/eventN` 不会出现，libinput 无法打开。
  测试时手动 `mknod` 后向 sysfs `uevent` 写 `add`，Phoc 才接管。Steam Input
  自建的 uinput 虚拟手柄与蓝牙手柄会遇到同一问题，独立会话需要补节点服务。
- **左上角箭头**：是 Phoc 的 Wayland 软件光标（`WLR_NO_HARDWARE_CURSORS=1`），
  停在最后一次指针位置；触摸和 XTest 都不移动它。Steam 内部无光标。
- **网络时间**：恢复环境的时钟落后九天，Steam API/Store 的 TLS 报“证书尚未生效”。
  对齐构建主机时间并把 SteamOS 时区设为 Asia/Shanghai 后，同一 API HTTPS 返回 200。

恢复后续：用户已确认进入 Android，ADB 再次识别为 SM-T870 / T870XXS8DXH1，
`ro.boot.flash.locked=0`、`verifiedbootstate=orange`、`sys.oem_unlock_allowed=1`；
DTB/DTBO 索引仍为 `0/6`、硬件 revision 仍为 `7`。因此 Download 页的
`OEM LOCK: ON` 不能单独用来要求再次解锁或清除数据。

此固件的 `adb reboot bootloader` 实测进入 Fastboot，USB 为 `18d1:d00d`。
该接口可以查询 product、unlocked、分区容量，实测拒绝 `fastboot boot` 的
下载命令及 `fastboot reboot`（`unknown command`）；没有由此写入镜像。
后续应从 Android 使用 `adb reboot download` 进入三星下载模式。准备恢复的
`out/gui/recovery-hci-update.img` 与此前真机读回哈希 `b2f2da4a…51b4ee` 一致，
AVB 签名与内容哈希通过；与工作 Lineage Recovery 对比，v2 头部参数、DTB 和
Recovery DTBO 均保留。原厂 BOOT 保留作回退。

使用 `adb reboot download` 后已通过 Heimdall 两次写入上述同一 Linux 镜像：
一次自动重启到 Android 再请求 Recovery，一次使用 `--no-reboot` 后由用户直接
按键进入 Recovery。两次都报告传输成功，但都出现三星 Recovery 与
`18d1:d001` 未授权 ADB，没有原生 RNDIS/ACM。后一次对照说明，不能仅把问题
归因于 Android 自动还原。尚未取得分区读回，写入结果和启动校验须继续区分；
随后改用历史成功使用的 samloader，保持镜像不变，已恢复 Linux 并取得完整分区读回。
这里只能确认两种写入流程在本机的结果不同，不能凭此断言具体 USB 协议缺陷。

## ARM64 Steam 客户端安装准备

`install-steam-arm64.sh` 调用同目录的 `extract-steam-arm64.py`。
官方客户端 ZIP 中有 Unix 符号链接使用反斜杠文件名；实测普通 `unzip` 返回 0，
却在根目录生成带反斜杠的文件名，`steamrtarm64/libs/libcurl.so` 并未建立。
新的解包器统一路径分隔符、还原并核对运行时内部的库链接，拒绝路径越界和错误
架构，先在临时目录完成校验，再替换运行时；已有运行时改名保留以便恢复。
解包官方客户端后的三条库链接及 ARM64 ELF 检查通过。

安装器保留已有 `config.vdf`、注册表和 Steam 库，不再关闭官方更新及 checksum
检查。新建注册表使用实际空白字符，避免旧脚本把字面量 `\\t` 写进 VDF。
这些改动只涉及 SteamOS 安装准备，不部署或修改现有 GNOME 输入与显示代码。
当前共 14 项主机测试通过（4 项文件系统盘点、6 项客户端安装、4 项内核配置与模块选择回归）。安装和游戏
界面仍须在设备上验收，主机动态加载检查不能证明 GPU 或实际游戏可用。

证据位于忽略目录 `out/steamos/`：`inventory.txt`、`bash-qemu.txt`、
`gamescope-qemu.txt`、`progress-20261024.json`；下载的主机工具隔离在其 `tools/`
子目录，没有安装到主机系统目录。镜像未提供可读的 pacman 本地数据库，不能
据此推断已安装软件数量或具体软件包版本；上面的版本来自实际文件或程序输出。

## 架构：SteamOS 唯一系统，gamescope 直驱面板

目标（2026-10-24 用户决定）：SteamOS 作为唯一系统，最终做成可由 Recovery 刷入的
包；gamescope 直接持有 KMS，不再经过 Phoc/sway 宿主；Ubuntu/GNOME 最后移除。

```
BOOT 分区：4.19 厂商内核（DEVTMPFS/FHANDLE 等）+ initramfs
└─ systemd（SteamOS rootfs，userdata 上）
   ├─ 硬件服务单元：Wi-Fi(cnss)、蓝牙 H4、传感器、背光、电源
   └─ gamescope-session：私有 gamescope --backend drm（双 VIG）
      └─ steam -gamepadui -steamos3
RECOVERY 分区：保留官方 Lineage Recovery，用于刷入/恢复
```

面板是 1600×2560，DPU 单个 VIG 只能输出 800 像素宽的一半；两个 VIG 必须在
**同一次原子提交**里。原先只有 `wlroots-sm-t870-split.patch` 实现这一点，现在
[gamescope-3.16.30-sm-t870-split.patch](../../userspace/steamos/patches/gamescope-3.16.30-sm-t870-split.patch)
把它移植进 gamescope DRM 后端，由 `GAMESCOPE_SM_T870_SPLIT=65,86` 显式启用：

- 只接受 `msm_drm`，按 ID 解析两个带 `zpos` 的 VIG；DSI CRTC 必须同时可达两者，
  扫描格式取两者交集；未指定输出时优先 `DSI-1`（SDE 的 `Virtual-1` 回写也报告已连接）。
- 每帧始终整屏 Vulkan 合成，旋转在合成器内完成；一个 1600×2560 FB 以 SRC/CRTC
  偏移 0/800 写入两个 VIG，zpos 均为 0，同次提交；其他挂在该 CRTC 的平面摘除；
  熄屏时显式摘除这对平面。SDE `alpha` 范围是 0–255，不写 AMD 的 16 位值。
- KGSL Turnip 没有 `VK_EXT_physical_device_drm`：此配置下用 SDE 的 `card0` 作为
  primary、`renderD128` 作为 render node（wl_drm/dmabuf feedback），否则客户端
  Wayland WSI 拿不到格式（vkcube 交换链参数为垃圾值，即此前嵌套崩溃的根因）。
- 关闭 modifier：KGSL 没有可导出的带 modifier 扫描图像，走线性 WSI scanout，
  与 wlroots 的 `WLR_DRM_NO_MODIFIERS=1` 一致。

构建：[build-steamos-gamescope-native.sh](../../scripts/build/steamos/build-steamos-gamescope-native.sh)
在设备 SteamOS rootfs 内用其自带 gcc 15.1/meson 原生编译（rootfs 带 255 MB 头文件），
只安装到 `/usr/local/lib/tabs7linux/gamescope-sm-t870`，不覆盖 `/usr/bin/gamescope`。
源码包含子模块与预取的 glm/stb，构建时不联网；解包用 `tar -m`。

**真机验收（2026-10-24，Phoc/GNOME 停止后单独运行）**：
- vkcube：读回 VIG 65、86 同挂 CRTC 110、同一 FB；用户确认立方体可见。
- Steam：gamescope 以 UID 1000 经 SteamOS 自带 seatd（`SEATD_VTBOUND=0`）运行，
  `--steam --force-orientation right`，Steam `-gamepadui -steamos3` 为子进程；
  用户确认**满屏、方向正确、触摸准确**（`left` 为上下颠倒）。
- 仍是 Ubuntu 侧 BusyBox/chroot 环境里的手动测试，不是 systemd 启动；
  退出时 `finish_drm` 的清理提交返回 EINVAL，尚未处理；无 PipeWire、Xwayland
  glamor 回退软件、未登录、未跑游戏。

两条约束仍然成立：**GPU 走 KGSL**（SteamOS 自带 Mesa 只适用于主线 MSM，使用私有
Turnip `turnip-steamos-wsi`）；**内核不变**（4.19 厂商内核，只改配置）。

此前"4.19 没有 PSI/cgroup v2，不能用 systemd"的判断是错的：实测内核有
`CONFIG_PSI=y`、挂载 cgroup2，SteamOS 为 systemd 257.7。真正缺的是
`DEVTMPFS`、`FHANDLE`（systemd 必需），以及 `CGROUP_PIDS`、`USER_NS`/`PID_NS`
（Steam Runtime 容器需要），可按 SYSVIPC 的方式重建内核配置。

**S3 内核进展（2026-10-24）**：配置片段
[steamos-systemd.config](../../hardware/samsung/gts7lwifi/kernel/configs/steamos-systemd.config)
在 IPC 内核上只增加 `DEVTMPFS`、`FHANDLE`、`CGROUP_PIDS`、`USER_NS`、`PID_NS`、
`AUTOFS_FS`（`DEVTMPFS_MOUNT` 保持关闭），对照 systemd 257 README 的必需项；
`AUDIT` 计划用 `audit=0` 处理。`verify-ipc-kernel.py --policy steamos-systemd`
通过：配置差异与审查一致，H4 模块重建（`42360eb2…`，45 个导入 CRC 匹配），
内存占用 +2.1 MB。蓝牙按配置哈希表选择模块。该内核已经写入 BOOT 并在真机启动，
TWRP 已写入 RECOVERY，见 [原生启动测试](../boot/native-boot-tests.md)。

### SteamOS systemd 启动（2026-10-24）

BOOT 中的 initramfs 由 [build-steamos-initramfs.py](../../scripts/build/vendor/build-steamos-initramfs.py)
重建（与旧 ramdisk 逐项比对，只有 `init` 与 `sbin/start-persistent-gui` 改变；后者同时带入已提交
但此前未部署的 schedutil 调速器设置），由 [update-linux-boot.py](../../scripts/build/vendor/update-linux-boot.py)
做 ramdisk-only 更新（布局检查：DTBO=Lineage、RECOVERY=TWRP、vbmeta flags=3）。BOOT 读回
`f65cde70…`；当时未带标志文件时日志为 "SteamOS not selected"，Ubuntu 照常启动。

> **启动目标已固定为 SteamOS（2026-10-24 后续）**：`boot-target` 选择机制与
> Ubuntu 出口一起删除。`start-persistent-gui` 现在只做一件事：校验 userdata 与
> `tabs7linux/steamos` 树，bind 到 `/newroot`、挂 devtmpfs，然后 `switch_root`
> 到 systemd。旧路径 `tabs7linux/ubuntu/steamos` 会被一次性原子重命名到
> `tabs7linux/steamos`；校验失败则停在前台 RAM 环境（USB RNDIS/ACM、telnet）并写
> `/run/steamos-bootstrap.log`（成功时该日志会复制到
> `/newroot/var/log/tabs7linux-initramfs/`）。迁移后 `tabs7linux/ubuntu` 里只剩
> 旧 Ubuntu 根文件系统，确认 SteamOS 能启动后再从 RAM shell 里手工删除。
>
> 写 BOOT 前跑 [preflight-steamos-boot.py](../../scripts/build/vendor/preflight-steamos-boot.py)：
> 它校验同一次启动的四分区快照（manifest 哈希、PIT 容量、DTBO/RECOVERY 布局、实时 DTB、
> board-id/msm-id）、已部署 BOOT 的 AVB 签名与 header v2，然后用该 BOOT 的 ramdisk 重建
> initramfs（只允许 `init` 与 `sbin/start-persistent-gui` 变化）并只替换 ramdisk 重打包，
> 把新的 BOOT、`preflight.json` 和回滚镜像写到 `out/`。全程不访问设备。
> 2026-10-24 已在真机执行：BOOT 写入 `0eb66dea…`，读回一致，启动日志显示树从
> `tabs7linux/ubuntu/steamos` 原子迁移到 `tabs7linux/steamos`，PID 1 为 systemd、
> Game Mode 单元 active。详见[原生启动测试](../boot/native-boot-tests.md#steamos-only-initramfs-真机结果2026-10-24)。

适配由 [install-systemd-adaptation.py](../../scripts/steamos/install-systemd-adaptation.py)
安装（每个改动备份并记录，`--uninstall` 精确还原；测试见
`tests/steamos/test_systemd_adaptation.py`），内容在 `userspace/steamos/systemd/`：
屏蔽 Steam Frame 硬件/VR/A-B/RAUC/USB gadget/firewalld/日志上报、`/home` 分区相关的
offload 绑定与 `etc.mount`；USB telnet 调试单元（sysinit、emergency、rescue 均拉起）；
NetworkManager 不管理 `usb0`，`.link` 固定接口名；journald 持久化。

**真机结果**：PID 1 为 SteamOS systemd（主机名 `frame`），dbus-broker、NetworkManager、
seatd、logind 运行，调试 shell 可用。发现两项并已加入屏蔽表：
- `systemd-repart.service` 带 `repart.d/90-home.conf`，会尝试在 `/` 所在磁盘（平板主 UFS
  LUN，含全部 Android 分区）新增 home 分区。本次 20 ms 内失败退出；读取 GPT 确认仍为
  35 项、末项 userdata、CRC 正确、已占满可用扇区，分区表未变。
- fstab 的 by-partsets `/home`、`/efi`、`/esp` 设备不存在，`nofail` 仍会等满 90 s 并拖住
  `local-fs.target`；屏蔽对应生成单元后启动继续。

**Game Mode 层（已部署，2026-10-24）**：`install-systemd-adaptation.py --game-mode` 从 Ubuntu 树按
清单哈希复制 6 个 Adreno 650 固件，安装 KGSL/ION udev 规则（`render` 组 0660）、面板平面 ID
`/etc/tabs7linux/sm-t870-split`、`tabs7-gamescope-session.service`（User=steamos、PAM login、
`SupplementaryGroups=seat`）并把默认目标设为 graphical。真机上由 systemd 拉起的 gamescope
驱动双 VIG（读回 65/86 同 FB），Steam 在 gamescope 内运行，曾显示已登录的主页。部署中逐个解决：

- 镜像没有 `/etc/shadow`、`/etc/gshadow`（原系统在 /etc overlay 首次启动时生成），PAM 报
  "could not obtain user info"；安装器按 pwconv/grpconv 生成，所有口令锁定（`!*`）。
- SteamOS 的 `seatd.service` 默认 VT 绑定，本内核无 VT，seat 永不激活，打开 card0 返回 EPERM；
  drop-in 设置 `SEATD_VTBOUND=0`。
- gamescope 的 `init_pipewire()` 无限等待 PipeWire 分配节点，且在启动子进程之前；用户会话里
  该节点曾一直不出现，Steam 从未启动。补丁改为最多等 5 s，失败则释放 PipeWire 对象、关闭
  截屏后继续（截屏可用时照常）。
- SteamOS 启动路径不经过 initramfs 的 build-epoch 时间回退，无网络时时钟停在 systemd 构建
  日期（2026-02），meson 报时钟偏差；本次按 NTP 校正过的偏移手动设时。需要网络或时间服务修复。
- 本镜像 `VARIANT_ID=vr`（Steam Frame）。登录后 Steam 进入 VR 变体模式（打开 VRStream 窗口、
  尝试 VR runtime），实测触摸与 XTest 鼠标在主页只产生悬停、不触发点击（X 指针随四角触点正确
  移动，焦点窗口正确）。会话单元以 `BindReadOnlyPaths=` 只给 gamescope/Steam 提供去掉
  VARIANT 行的 os-release，系统身份不变；Steam 随后报告 `variant:`（空）。该改动后的触摸效果
  **尚未验证**：SteamOS 下没有网络，Steam 离线启动停在加载圈。
- `steamos-manager` 的 Frame 配置只匹配 `qcom,sm8650`，在本机报告 DeviceModel "unknown"，
  不是 VR 模式的来源；屏蔽它会使 `-steamos3` 客户端无法完成启动，已恢复。

**当前阻塞**：SteamOS 里没有网络（Wi-Fi 依赖的 cnss-daemon、固件与 IPA 目前只在 Ubuntu 侧
启动），Steam 离线启动不稳定，无法验证主页触摸。下一步是 S4 的 Wi-Fi 迁移。
旧的 sway 宿主方案文件 `userspace/steamos/session.sh`、`start-app.sh`、`sway.conf` 已不再使用。

## 与 SteamOS 的已知差异

| SteamOS 预期 | 本机做法 |
| --- | --- |
| systemd PID 1、logind seat | 目标同 SteamOS；当前测试用 SteamOS seatd（`SEATD_VTBOUND=0`） |
| VT 切换、fbcon | 内核无 VT，从未使用 |
| `/etc` 只读 + overlay | 目录直接可写，保留 `/var/lib/overlays/etc/upper` 只是兼容 |
| `/boot` FAT + ABL | 目标为 BOOT 分区 + initramfs；Recovery 保留 Lineage |
| RAUC 原子更新 | 不可用；固定 0.5.3，Steam 客户端自行更新 |
| Mesa 26（MSM） | 私有 Mesa 25.2.8（KGSL），ICD/EGL 显式指定 |
| gamescope 原版 DRM 后端 | 私有双 VIG 补丁版 |
| 每设备 DTB/内核 | 现有 4.19 内核与 DTB 不变 |

## 里程碑

| 阶段 | 内容 | 验收 | 状态 |
| --- | --- | --- | --- |
| S0 | 抓取并校验官方 rootfs | 镜像哈希一致 | 完成 |
| S1 | chroot 内运行 SteamOS 二进制 | aarch64 程序在 4.19 上执行 | 完成 |
| S2 | gamescope 直驱双 VIG + Steam Game Mode | 满屏、方向正确、触摸准确 | 手动测试通过 |
| S3 | 内核加 DEVTMPFS/FHANDLE/CGROUP_PIDS/USER_NS/PID_NS，SteamOS systemd 为 PID 1 | 冷启动进入 Game Mode | systemd 已为 PID 1 启动到 multi-user；Game Mode 层未部署 |
| S4 | 硬件服务迁入 SteamOS systemd 单元，移除 Ubuntu | Wi-Fi/蓝牙/传感器/背光/电源可用 | 未开始 |
| S5 | BOOT 分区 + Recovery 可刷入的安装包 | Lineage Recovery 刷入后冷启动 | 未开始 |
| S6 | FEX + Proton-arm64 跑通一个 x86 游戏 | 记录无 fsync 时的帧率与兼容性 | 未开始 |

## 风险

- **内核 4.19 缺 `futex_waitv`（5.16+）与 `ntsync`（6.14+）**：Proton 的
  fsync/esync 不可用，Wine 退回 server 同步。这是 x86 游戏性能的硬上限，换 rootfs
  不解决。
- **SteamOS 二进制的系统调用基线**：新 glibc 程序可能使用 `clone3`、`openat2`、
  `pidfd` 等 4.19 没有的接口。需要逐个实测，缺失时只能等主线内核。
- **私有 Mesa 与 SteamOS 库的版本组合**：Mesa 25.2.8 按 Ubuntu 24.04（glibc 2.39）
  编译，SteamOS 0.5.3 同为 2.39，ABI 方向安全；但 libdrm/wayland 的符号版本仍需
  在 S1 核对。
- **gamescope 构建**：需要在设备上按 SteamOS 的库编译，或在 Ubuntu 侧编译后跨
  rootfs 运行。两条路都要验证符号版本。
- **Steam 库空间**：rootfs 约 4 GiB，Steam 库另计；userdata 余量不足时使用
  microSD。

## SteamOS 私有 Wayland WSI 修复

原 KGSL-only Mesa 25.2.8 构建没有 `HAVE_LIBDRM`，但 Wayland 仍给公共 WSI 传入
DRM 类型图像参数。release 优化后落入 CPU 参数处理，把 `same_gpu=true` 解释为
`alloc_shm=0x1`，原生 GDB 记录为 `SIGBUS/BUS_ADRALN`。`MESA_VK_WSI_DEBUG=sw`
对照能绘制，但只是共享内存呈现诊断，不是交付方案。

简单改成 `freedreno-kmds=msm,kgsl` 会额外声明 `KHR_display`，而 KGSL 初始化
明确拒绝这个扩展。最终专用补丁保留 DRM 图像代码，却不声明直接显示扩展：
`userspace/steamos/patches/mesa-25.2.8-kgsl-wayland-wsi.patch`。
构建入口为 `scripts/build/steamos/build-steamos-turnip-native.sh`，只安装到独立
SteamOS 目录的 `turnip-steamos-wsi` 前缀，不覆盖 Ubuntu/GNOME 私有驱动。
原生 `vulkaninfo` 与直接 Wayland 立方体均通过，库 SHA-256 为
`95bd008407347c966fd460ba27f73441edc63940e7dd2db03a30e23d8729b1e7`。

完整 Game Mode、触摸操作和游戏尚未验收，不把这些准备项记作 S2/S3 完成。

## IPC 内核与外部模块

`hardware/samsung/gts7lwifi/kernel/configs/steamos-ipc.config` 仅启用 Steam 实际调用的
System V IPC。不能只替换内核后继续用旧模块：SYSVIPC 改变 `task_struct`，本次有
8,253 个既有导出符号的 CRC 改变。实时模块快照只有 `tabs7_hci_uart`，已用同一
候选内核配置重建，并核对其全部 45 个导入。

- 原内核配置及旧 H4 模块保留，按完整配置哈希选择匹配模块。
- 新 H4 模块 SHA-256：`60bf0936d04bc1efd1251aecb09b8e3e7b1a1390dcd8b80217ae450504c1d30a`。
- `scripts/steamos/verify-ipc-kernel.py` 检查实际 Image 配置、HCI 修复、CFI、
  符号与实时模块清单。新增模块必须重新审查，不能忽略版本检查。
- `repack-vendor-kernel.py --config-policy steamos-ipc` 只接受上述精确配置差异；
  默认策略仍要求原配置完全不变。封装检查真实 FDT 保留区、同次启动分区备份、
  内存窗口、v2 头部、ramdisk、DTB、Recovery DTBO 和 AVB 签名。
- 已回读的新 Recovery 只替换 kernel；原厂 BOOT、DTBO、VBMETA 与 userdata 未写。

## Steam 更新与运行时目录

官方清单中的 `linuxarm64`、`steamrt64`、`steamrtarm64` 是三套不同的文件，不能
互相软链接。旧项目安装器创建的 `steamrt64 -> steamrtarm64` 和
`linuxarm64 -> steamrtarm64` 会使官方更新器覆盖另一套运行时，导致反复校验失败。
安装器现在只清理这两个精确的旧链接，保留真正的目录与其他自定义链接。
真机已拆开旧链接，由官方更新器重新填充；未绕过校验或禁用更新。

`userspace/steamos/steam-client.sh` 直接启动 ARM64 原生客户端，处理官方更新后
要求重启的退出码 42。它不依赖镜像里指向缺失文件的 `/usr/bin/steam` stub。
独立 Game Mode 仍须完成触摸、键盘与显示会话验收，现阶段保留 GNOME 启动配置。
