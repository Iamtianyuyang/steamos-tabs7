# 内核迁移：厂商 4.19 → 主线 6.2

SteamOS 的目标内核是主线 6.2（`linux-sm8250-v6.2/`）。**6.2 至今没有在真机启动过**：
现在设备上跑的每一项目标都仍由 Lineage/Samsung 4.19 内核提供。这份文档记录迁移的
分层方式、已经落地的部分、以及按依赖顺序排出的阻塞项。

## 构建

```sh
scripts/build/mainline/build-steamos-6.2.sh config   # 只验证配置
scripts/build/mainline/build-steamos-6.2.sh dtb      # DTB
scripts/build/mainline/build-steamos-6.2.sh image    # DTB + Image/Image.gz（默认）
scripts/build/mainline/build-steamos-6.2.sh image \
    hardware/samsung/gts7lwifi/kernel/configs/steamos-display-6.2.config  # 追加显示层
```

脚本把 `hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config` 与
`steamos-6.2.config` 合并（后者覆盖前者），用 `allnoconfig` 生成配置，
**逐条核对每个请求的符号是否真的保留**，然后把 DTB、`Image`、`Image.gz`、
`kernel.config`、`config-review.txt` 和 `manifest.json` 写到
`out/steamos-kernel-6.2/`。缓存目录是
`~/.cache/tabs7linux/steamos-6.2-<项目路径哈希>/`（`TAB_S7_STEAMOS_BUILD_ROOT` 可覆盖）。

当前结果（clang 18.1.3 / LLD 18）：

| 产物 | SHA-256 |
| --- | --- |
| `Image` | `8d3ac5c1642d53cdef2e2af0c56fc24854304db70852aa2272ff1d9afd1786f5` |
| `Image.gz` | `7635741b069f41fcbe708fb9fede92fe41974c2820d9e409f67511c7184be293` |
| `sm8250-samsung-gts7lwifi.dtb` | `00268a9a1474d2fbef377ef188ccfa2db099df9a9c812c6376806496529463c2` |

122 项请求全部保留。产物只是编译结果，不是可启动镜像。

## 配置映射

| 4.19 厂商片段 | 6.2 片段 | 说明 |
| --- | --- | --- |
| `steamos-ipc.config`：`SYSVIPC`、`IPC_NS` 关闭 | `SYSVIPC`、`SYSVIPC_SYSCTL`、`SYSVIPC_COMPAT`，`IPC_NS` 仍关闭 | Steam 客户端调用 `semget()`；`IPC_NS` 沿用 4.19 的决定，尚未在 Steam Runtime 里验证过开启 |
| `steamos-systemd.config`：`DEVTMPFS`、`FHANDLE`、`CGROUP_PIDS`、`USER_NS`、`PID_NS`、`AUTOFS_FS` | 同名符号，追加 `CGROUPS`、`NAMESPACES`、`NET_NS`、`UTS_NS`、`TMPFS_XATTR`、`TMPFS_POSIX_ACL` | `allnoconfig` 会关掉 `CGROUPS`/`NAMESPACES`，而 `CGROUP_PIDS`/`USER_NS` 依赖它们，不显式打开就会被 Kconfig 静默丢弃 |
| `DEVTMPFS_MOUNT` 关闭 | 同样关闭 | initramfs 自己在新根上挂 devtmpfs，内核先挂会打架 |
| 厂商内核内建 F2FS | `F2FS_FS=y` | 交接步骤要先挂 userdata 才能找到 SteamOS 树，6.2 默认没有 |

## 能带过去的与带不过去的

| 组件 | 4.19 做法 | 6.2 上的状态 |
| --- | --- | --- |
| systemd / cgroup v2 / logind | 4.19 + 补的 `DEVTMPFS`、`FHANDLE`、`CGROUP_PIDS`、`USER_NS`/`PID_NS` | 6.2 主线原生支持，配置已就位 |
| HCI socket | 厂商内核禁用了它，需要 `hci-socket-restore.patch` 重建内核 | 主线本来就启用，补丁不需要 |
| 热插拔节点 | 厂商没有 `DEVTMPFS`，靠手工 `mknod` + 写 `uevent` | 主线有 `DEVTMPFS`，`systemd-udevd` 正常建节点 |
| VT / seatd 变通 | 厂商内核无 VT，用 `SEATD_VTBOUND=0` | 主线有 VT/DRM，变通可保留但不再是必需 |
| GPU（Turnip KGSL → Zink → wlroots/gamescope） | 私有 Mesa 走厂商 `KGSL`，双 VIG 由私有 wlroots 补丁拆分 | **主线没有 KGSL**，必须换成上游 `msm` + freedreno/Turnip（KMS）。整套私有前缀 `turnip-kgsl/`、`mesa-zink*`、`gamescope-sm-t870` 的构建前提变了 |
| 面板与双 VIG | 厂商 DSI/DPU 行为 + `WLR_DRM_SM_T870_SPLIT=65,86` | 面板驱动不在 6.2 主线，DPU 的平面布局也要在 `msm` 上重新确认；两平面拆分补丁本身是 wlroots 侧的，可复用性待验证 |
| 触摸 | 厂商 `sec_touchscreen`/nt36523 | 6.2 主线没有该驱动 |
| Wi-Fi / 蓝牙 | 厂商 `qcacld` + CNSS + 私有 NetworkManager | 主线路线是 `ath11k` + `hci_qca`，与现有 `userspace/platform/network/`、`platform/bluetooth/` 的适配对象不同 |

## 按顺序的阻塞项

1. ~~**initramfs 找不到 userdata**~~（已实现，待 6.2 真机验证）。`start-persistent-gui`
   现在先读 `/sys/class/block/*/partlabel`（主线的 GPT 名称，需 `CONFIG_EFI_PARTITION=y`，
   已加入本层的 123 项请求），没有该属性时回退到厂商内核的 `uevent PARTNAME`，命中后
   仍然按 F2FS 双超级块 magic 与 UUID 校验。2026-10-24 在 4.19 上验证了回退分支
   （日志为 `userdata matched by PARTNAME`）；**partlabel 分支要等 6.2 首次启动才会第一次
   真正执行**，因此在 6.2 上这条路径属于未验证代码。
2. **显示**：面板参数、6.2 树里已有的部分、C-PHY/时序/初始化序列的九项差距和按依赖排的
   工作顺序见[面板与双 VIG 评估](panel-6.2.md)。结论是 6.2 的 DPU 与 DSI 框架都在，
   但 C-PHY 使能路径、每面板 PHY 调优、359 条初始化序列、时序与 lane 组合都还没对上。
3. **GPU 用户空间**：Turnip-KGSL 链路在 6.2 上不成立，需要重建 freedreno/Turnip(mSM) +
   gamescope 的构建与库布局，`userspace/steamos/gamescope-session` 里的
   `TABS7_ZINK_KGSL=1`、`mesa-zink-gnome-x11` 前缀都要改写。
4. **输入与网络**：触摸、Wi-Fi、蓝牙都需要对应的主线驱动与固件路径。
5. **BOOT 封装**：6.2 的 `Image` 能否沿用现有 v2 header/AVB 封装与命令行参数（`rdinit=`、
   `androidboot.*`）需要在写入前用 `preflight-steamos-boot.py` 的同类检查重新确认。

在 1–4 任一项在真机上通过之前，不要把 6.2 产物写成"可启动内核"，也不要覆盖 4.19 的
BOOT（当前 `0eb66dea…`）。

## 6.2 BOOT 候选（已生成，未刷写）

`scripts/build/mainline/build-steamos-6.2-boot.py` 把 6.2 内核打进 BOOT：沿用设备上
正在用的 BOOT 的 header v2 参数与命令行，只替换三个载荷（`Image`、DTB、
initramfs），用同样的 rollback index/location 重新签 AVB。

```sh
scripts/build/mainline/build-steamos-6.2-boot.py \
    --snapshot out/steamos/native-preflash3 \
    --deployed-boot out/steamos/boot-preflight-20261024-145309/boot/boot.img \
    --kernel out/steamos-kernel-6.2/Image \
    --dtb out/steamos-kernel-6.2/sm8250-samsung-gts7lwifi.dtb \
    --ramdisk out/steamos/boot-preflight-20261024-145309/boot/initramfs.cpio.gz \
    --output out/steamos-kernel-6.2/boot
```

写入前它会依次核对：同一次启动的四分区快照（manifest 哈希、PIT 容量、DTBO/RECOVERY
布局、实时 DTB、board-id/msm-id）、已部署 BOOT 的 AVB 签名与 header v2、内核确实是
6.2、DTB 里确实有面板节点，最后核对重打包后只有三个载荷变化、header 与命令行逐项
一致、镜像正好等于 BOOT 分区容量。

当前产物：

| 项 | 值 |
| --- | --- |
| `boot.img` | `out/steamos-kernel-6.2/boot/boot.img`，`46752485…0e9c5148` |
| 大小 | 71,303,168 字节（与 BOOT 分区容量完全相同） |
| 内核 | `Linux version 6.2.0`（clang 18.1.3），显示层配置 |
| DTB | `1a14781f…`，含 `samsung,gts7l-nt36523` 面板节点 |
| initramfs | 与 4.19 上正在运行的那个相同（partlabel/PARTNAME 双探测） |
| manifest | `out/steamos-kernel-6.2/boot/manifest.json`，`hardware_boot_observed: false` |

刷写这个镜像会把当前稳定运行的 4.19 SteamOS 换成**从未启动过的 6.2 内核**：失败时
预期落在 initramfs 的 USB/telnet 环境（面板不亮也还能进），但没有任何真机证据。
所以脚本只生成镜像，写盘仍要单独授权；回退镜像是
`out/steamos/boot-preflight-20261024-145309/boot/boot.img`（`37f5aa13…`）。

## 6.2 首启失败诊断（2026-10-24 实测）

- **免 ODIN 的写入通道已打通**：`scripts/device/push-boot.py` 通过 RNDIS 把镜像推到
  运行中的 SteamOS（内置 TCP 接收器 → 校验哈希 → `dd` 到 `/dev/disk/by-partlabel/boot`
  → 读回比对）。71 MB 传输 + 写入 + 读回共约 10 s，完全不碰 ODIN。
- **计时实验**：从 SteamOS 写入 6.2 候选（`ee332fa7…`）后重启，USB 状态为
  0–12 s 无设备、**14 s 起稳定停在 Download mode（04e8:685d）**，全程未出现
  initramfs 的 RNDIS gadget（1d6b:0104）——说明 6.2 的 initramfs 从未运行。
- `/sys/fs/pstore` 为空：即使 `ramoops@9fa00000` 在 DTB 里、`PSTORE_RAM/CONSOLE/PMSG`
  都开着，也没有任何记录 → 内核没有走到 pstore 初始化，或根本没被执行。
- `misc` 的 BCB 前 96 字节全零，`param`/`misc` 里没有失败原因字符串。
- 已知对照组：同一写入通道写回 `37f5aa13…`（vendor 4.19 内核 + vendor DTB）立即正常
  启动，说明**写入路径和分区都没问题**，问题在 6.2 这套"内核 + DTB"组合。

### 下一步的两个隔离实验（镜像已生成，等待上机）

| 实验 | 内核 | DTB | 产物 | 判读 |
| --- | --- | --- | --- | --- |
| A | vendor 4.19 | 6.2 主线 | `out/steamos-kernel-6.2/exp-vendor-kernel-6.2-dtb/boot.img` `9b7d8d58…` | 若仍停在 Download → **6.2 的 DTB 就是触发点**（厂商 DTBO overlay 无法应用到主线 DTB，LK 视为启动失败） |
| B | 6.2 | vendor 4.19 | `out/steamos-kernel-6.2/exp-6.2-kernel-vendor-dtb/boot.img` `ec62fe22…` | 若能出现 gadget → 内核本身可执行，问题集中在 DTB/overlay；若同样 Download → 内核在这块板子的早期初始化就挂了 |

实验 A 的背景：仓库里的既有记录已指出"原厂 Android overlay 对主线 DTB 应用失败
（`FDT_ERR_NOTFOUND`）"，本项目为 recovery 草稿专门做过空操作 overlay。LK 在 BOOT
启动时同样会把 DTBO 分区的 overlay 应用到 BOOT 里的 DTB，这很可能是 14 秒后落回
Download 的原因。
