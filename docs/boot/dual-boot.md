# 双系统：保留 Android + 在 recovery 里切换

设备上 RECOVERY 分区装的是第三方 recovery（TWRP/PBRP，当前读回
`68214dc9…c64a5e46`，86,888,448 字节）。它就是双系统的"开关"：BOOT 分区放
Android 还是 Linux，由它写的字节决定。

## 为什么用 recovery 而不是 ODIN

- ODIN 每次写入要重新握手，自定义内核启动失败后会话会坏掉，需要断电重启；
- recovery 里是 root shell，直接 `dd` 写分区，不受签名通道限制；
- 切换是一次分区写入 + 读回哈希，十几秒完成。

recovery 自身的"固定"：只要不刷完整官方固件（会覆盖 RECOVERY），它就一直留在
RECOVERY 分区；Android 不启动时也不会有 `recovery-from-boot` 覆盖它。

## 准备（主机，离线）

```sh
scripts/device/prepare-dual-boot.py \
    --android-boot out/stock-firmware/T870XXS8DXH1/boot.img \
    --linux-boot   out/steamos/boot-preflight-20261024-145309/boot/boot.img \
    --recovery     out/steamos/native-preflash3/recovery.img \
    --output       out/dual-boot
```

脚本校验：两张 BOOT 都是完整分区大小、ARM64 magic 正常；Linux BOOT 的 AVB 用项目
testkey 验签通过；recovery 也是完整分区大小。产物：

| 文件 | 内容 |
| --- | --- |
| `android.img` | 原厂三星 BOOT（`f555ff2b…`），Android 从它启动 |
| `linux.img` | 项目 BOOT（`37f5aa13…`），initramfs → SteamOS |
| `recovery.img` | 第三方 recovery 全分区转储（`68214dc9…`） |
| `manifest.json` | 三者的大小与 SHA-256 |
| `switch-boot.sh` | recovery 侧切换脚本 |

## 切换（recovery 内）

```sh
# TWRP 的 /data/media/0 走 sdcard 封装，会报 secure_mkdirs 失败，改用裸 /data 路径
adb push out/dual-boot/. /data/tabs7linux/boot/
export TABS7_BOOT_STORE=/data/tabs7linux/boot
adb shell "TABS7_BOOT_STORE=$TABS7_BOOT_STORE sh /data/tabs7linux/boot/switch-boot.sh status"
adb shell "TABS7_BOOT_STORE=$TABS7_BOOT_STORE sh /data/tabs7linux/boot/switch-boot.sh linux"
adb shell 'twrp reboot'      # 必须用 twrp reboot；adb reboot 会再次回到 recovery
```

实测（2026-10-24）：`switch-boot.sh linux` 从 TWRP 写入 BOOT 并读回
`37f5aa13…`，`twrp reboot` 后 SteamOS 起来（4.19 内核、PID 1 systemd、
`tabs7-gamescope-session.service` active）。**这条路完全绕开 ODIN**，自定义内核
启动失败也不会影响它——当时设备正处于"Download 兜底"状态，TWRP 仍然正常启动。

脚本行为：找 `by-name/boot`、取分区字节数、校验镜像大小与 manifest 哈希、
`dd` 写入、`sync`、**读回比对哈希**，最后可选 `reboot`。任何一步不符就退出，不做部分写入。

## 恢复 recovery 自身

```sh
adb shell dd if=/data/media/0/tabs7linux/boot/recovery.img \
              of=/dev/block/bootdevice/by-name/recovery bs=1M
adb shell sha256sum /dev/block/bootdevice/by-name/recovery   # 应为 68214dc9…
```

## 数据布局的现实约束

Android 的 `/data` 和我们的 Linux 树（`tabs7linux/`）都在 userdata 上，两者不能同时
使用。当前 userdata 是 Linux 树；要让 Android 正常启动，它的首启会要求格式化
`/data`，**这会删掉 Linux 树与已装的 Steam 客户端**。三个可选方案：

1. Linux 树放 microSD（最省事，不动机器分区）；
2. 缩小 userdata、新建一个分区给 Linux（要改 GPT，风险高，需要备份）；
3. 接受 Android 首启清空 userdata，之后再把 Linux 树重建到 userdata。

在选定方案前，recovery 的用途是：备份 userdata 里的 Linux 树、写回 `linux.img`
恢复 SteamOS、以及随时切换 BOOT。

## 风险

- 写 BOOT 时断电会留下半写分区；脚本只在完整镜像上操作，回退镜像是 `android.img`
  或 `linux.img`，用 recovery 再写回即可。
- 第三方 recovery 本身是自定义镜像，若 bootloader 进入"自定义镜像拦截"状态，
  **连 recovery 也可能拒绝启动**；此时只能断电重启后再试，或走 ODIN 恢复原厂镜像。
- 完整官方固件刷写会覆盖 RECOVERY 并恢复签名校验，等于丢掉这套双系统开关。
