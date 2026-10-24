# 内核迁移：厂商 4.19 → 主线 6.2

SteamOS 的目标内核是主线 6.2（`linux-sm8250-v6.2/`）。**6.2 至今没有在真机启动过**：
现在设备上跑的每一项目标都仍由 Lineage/Samsung 4.19 内核提供。这份文档记录迁移的
分层方式、已经落地的部分、以及按依赖顺序排出的阻塞项。

## 构建

```sh
scripts/build/mainline/build-steamos-6.2.sh config   # 只验证配置
scripts/build/mainline/build-steamos-6.2.sh dtb      # DTB
scripts/build/mainline/build-steamos-6.2.sh image    # DTB + Image/Image.gz（默认）
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
2. **显示**：6.2 主线没有这块面板的驱动，也没有验证过双 VIG 在 `msm`/DPU 上的平面布局。
3. **GPU 用户空间**：Turnip-KGSL 链路在 6.2 上不成立，需要重建 freedreno/Turnip(mSM) +
   gamescope 的构建与库布局，`userspace/steamos/gamescope-session` 里的
   `TABS7_ZINK_KGSL=1`、`mesa-zink-gnome-x11` 前缀都要改写。
4. **输入与网络**：触摸、Wi-Fi、蓝牙都需要对应的主线驱动与固件路径。
5. **BOOT 封装**：6.2 的 `Image` 能否沿用现有 v2 header/AVB 封装与命令行参数（`rdinit=`、
   `androidboot.*`）需要在写入前用 `preflight-steamos-boot.py` 的同类检查重新确认。

在 1–4 任一项在真机上通过之前，不要把 6.2 产物写成"可启动内核"，也不要覆盖 4.19 的
BOOT（当前 `0eb66dea…`）。
