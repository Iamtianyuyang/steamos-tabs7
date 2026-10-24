# SM-T870 原生 Linux 启动测试

设备为 `SM-T870 / gts7lwifi`、硬件版本 7，Bootloader `T870XXS8DXH1`。
用户于 2026-10-13 授权清空并继续，已经完成恢复出厂设置。测试目标为
Linux 6.2 和 RAM 内的 BusyBox 调试环境，通过 USB ECM/ACM 取得 shell。
显示、触摸和内部存储尚未启用。

## 第一次测试：没有 AVB footer

2026-10-13 14:28 UTC，从 root Lineage Recovery 写入完整的 RECOVERY 分区，
写入后 SHA-256 与主机文件完全一致，再执行 `adb reboot recovery`。

- Linux 草稿 SHA-256：`1a22712b8cba4c8bf749214bc32b365c97d0e8d02087d648c4200860763743eb`。
- 补零后的 86,888,448 字节分区镜像 SHA-256：`ab99cbb7501f16f8b1b758634cccf4c2d418c153e35b361a9722d9a26ab3eb16`。
- 原厂与实时内存保留区覆盖、硬件选择参数、DTBO 索引 6 和完整设备转储检查通过。
- 主机在重启约 19 秒后观察到 Samsung Download mode；没有观察到 Linux gadget `1d6b:0104`。
- 没有取得能证明 Linux 6.2 启动的日志。恢复启动后的 pstore 为空，不能据此确定内核是否曾执行。

首次恢复官方 Recovery 返回 `FAIL! Auth`。用户重启进入新的 Download 会话后，
匹配固件的三星原厂 Recovery 写入成功，并成功启动原厂 Android。Android
报告 `flash.locked=0`、`verifiedbootstate=orange`、OEM 解锁允许。随后通过
正常 Android 进入新的 Download 会话，再次安装并启动官方 Lineage Recovery。
不能仅凭一次传输中的 Auth 错误推断 Bootloader 已重新锁定。

日志和设备快照保存在 `out/logs/`、`out/device/after-native-test1/`。
同一个 Download 会话中使用 `--no-reboot` 后再次发起新握手曾失败；后续恢复
应通过完整重启建立新会话。原厂恢复文件始终保留。

## 第二版：补齐 AVB 格式

已启动的官方 Recovery 包含 AVB footer，并使用公开的 AOSP RSA4096 开发测试
密钥。该设置可在
[LineageOS SM8250 配置](https://raw.githubusercontent.com/LineageOS/android_device_samsung_sm8250-common/lineage-24.0/BoardConfigCommon.mk)
核对。第一版缺少 footer 是已确认的格式差异，是否为启动失败原因仍需测试。

```bash
python3 scripts/setup/prepare-avb-tools.py
python3 scripts/recovery/build-recovery-avb.py
```

工具和公开测试密钥固定到提交与 SHA-256。脚本从已校验的官方 Recovery
读取回滚索引、位置及公钥，确认开发密钥与参考镜像公钥一致，然后生成新的
签名与内容哈希，没有沿用不匹配的旧签名。回滚索引保持参考镜像已经使用的
`1790812800`，位置为 `0`。Linux 内核、initramfs、DTB 和 DTBO 与第一次测试
相同，只有 AVB 封装变化。

产物为 `out/bringup/recovery-linux-avb.img`，86,888,448 字节，SHA-256 为
`1d6e117ff1e5562fc958162d47923a7ffc5d5e90fc523e381444fe88c04d7f4a`。
已通过官方 avbtool 对参考与新镜像的签名、哈希验证，并检查 Linux payload
逐字节保持不变。这是使用公开开发密钥的测试镜像，尚未证明原生启动成功。

avbtool 根据 descriptor 的分区名读取 `recovery.img`，因此验证目录为
`out/bringup/avb-recovery/`。不要因导出文件名不同而将文件查找错误误判为
密码学验证失败。

2026-10-13 15:08 UTC，第二版也通过 root ADB 写入、完成全分区 SHA-256
读回，然后重启 Recovery。约 19 秒后再次进入 Samsung Download mode，
没有观察到 Linux USB 接口。用户抄录的红字为
`custom binary blocked by oem lock`；重新进入 Download 后屏幕显示
`OEM LOCK: OFF (U)`，没有看到 KG 字段。Android 此前也报告已解锁，不能
把该通用报错直接解释为需要再次解锁。立即恢复 Lineage 的传输返回 Auth
错误，后续必须使用新的 Download 会话。

## 第三版：工作镜像头部和 Download 写入

```bash
python3 scripts/recovery/build-recovery-draft.py --header-source lineage
python3 scripts/recovery/build-recovery-avb.py
```

新版本采用已成功启动的官方 Lineage Recovery 的 v2 header：ramdisk 地址
`0x01000000`、tags 地址 `0x100`、空 board 字段、OS 17.0.0 和补丁日期
2026-10，并保留其命令行后附加 Linux 调试参数。NUL 参数解析保留空值，
防止空 board 字段导致参数错位。内核、initramfs、DTB 和 no-op DTBO
payload 没有改变；AVB 元数据增加本项目自身的 recovery fingerprint。

86,888,448 字节镜像 SHA-256 为
`970733fd028f772fa96e4a3bc496bd3b5f5bda3bb0f866e77dac11bfb766f237`。
组件解包对照、AVB 签名及哈希检查通过。计划通过三星 Download 协议写入
RECOVERY，替代前两次的 root ADB `dd`。这次同时改变头部模板和写入方式，
结果不能单独证明哪一项差异是原因。

2026-10-13 15:38 UTC，Download 写入命令返回 0。用户按键启动后，主机
15:40:02 观察到 USB 断开，15:40:11 又识别为 Samsung Download，仍没有
Linux gadget。没有取得本次 RECOVERY 分区读回或 Linux 内核日志，不能
把传输成功等同于启动成功。

用户随后抄录屏幕 `OEM LOCK: ON (U)`、`FRP LOCK: OFF`、
`CURRENT BINARY: Custom (0x30C)`，与此前报告的 `OFF (U)` 不一致。
随后恢复匹配固件的三星 Recovery，Download 写入命令返回 0，Android
正常启动。用户确认「OEM 解锁」开关打开，实时 ADB 读取
`flash.locked=0`、`verifiedbootstate=orange`、`sys.oem_unlock_allowed=1`，
补充 OEM 检查后的预检通过。没有再次解锁或恢复出厂。屏幕状态报告差异
的原因仍不明确，现有证据不支持要求再次解锁。

日志为 `out/logs/flash-native-test3.log`、
`out/logs/native-test3-host-kernel.log`、
`out/logs/restore-factory-after-native-test3.log`；正常 Android 快照位于
`out/device/after-native-test3-android/`。

## 厂商内核原生 Linux 对照镜像

```bash
python3 scripts/build/vendor/build-vendor-linux.py
```

产物 `out/vendor-linux/recovery.img` 为 86,888,448 字节，SHA-256 为
`61590634578043470acafeaba3434f85ea33df139e8c17c61820d6fe3a0d7393`。
保留已成功启动的官方 Lineage 4.19 内核、DTB、Recovery DTBO 和全部头部
参数，仅替换 ramdisk，追加 `rdinit=/init printk.devkmsg=on`，重新生成
本项目 AVB 签名。组件还原、静态 ARM64 BusyBox、cpio 字符设备、AVB
签名和容量检查通过，同输入重建的完整镜像 SHA-256 一致。

运行环境完全在 RAM 中，不启动 Android init。参考内核没有 DEVTMPFS
和 ECM，因此 `/dev` 使用 tmpfs 加 mdev/mknod，USB 使用内建 RNDIS 和
ACM；IP 与 MAC 沿用主线测试环境。pstore 提前挂载且不清理。启动证据
应包含 USB shell 中的 `uname`、PID 1、挂载表和 `/init` 的内核日志标记。
这是厂商 4.19 内核上的原生 Linux 用户空间对照；即使成功，也不代表
主线 6.2 或完整桌面发行版已移植完成。

### 第四次测试：原生用户空间已启动

2026-10-13 15:52 UTC，从通过 OEM 解锁检查的正常 Android 进入新的
Download 会话，写入对照 RECOVERY，命令返回 0。用户按键启动 Recovery
后，15:53:56 UTC 主机识别 USB `1d6b:0104`，RNDIS `usb0` 和 ACM
`ttyACM0` 已枚举。NetworkManager 使用现有配置取得 `192.168.77.1/24`。

USB shell 的实际运行证据：

| 项目 | 结果 |
| --- | --- |
| 内核 | `4.19.325-cip136-st20-perf-g4478511c4b37`，`aarch64 GNU/Linux` |
| PID 1 | `/bin/sh /init`，可执行文件 `/bin/busybox` |
| 根与挂载 | RAM rootfs、proc、sysfs、tmpfs、devpts、pstore、configfs |
| 初始化日志 | 3.781541 秒到达 BusyBox `/init`；3.917292 秒完成 USB 配置 |
| UDC | `configured` |
| 分区读回 | `/dev/sda23` 完整 SHA-256 与镜像 `615906…d7393` 一致 |
| 分区容量 | 169704 个 512 字节扇区，即 86,888,448 字节 |
| 持续运行 | `/proc/uptime` 已观察到 239.35 秒 |

没有运行 Android init 或 Android 用户空间，未挂载 Android 系统分区。
用户看到的 Samsung 非官方系统提示仍留在屏幕上；此环境没有图形 UI，
该屏幕不能单独用来判断 Linux 是否启动。完整 runtime 证据保存在
`out/logs/native-test4-runtime-validation.log`，主机 USB 日志为
`out/logs/native-test4-host-kernel.log`。原生 Linux 的实时 FDT、dmesg、
启动阶段日志、挂载表和 pstore 位于 `out/device/native-vendor-linux/`。
FDT 的 board-id 为 `[8,7]`，DTB/DTBO 索引仍是 `0/6`。

### 使用当前调试环境

保持 USB 连接，在电脑执行：

```bash
telnet 192.168.77.2
```

Recovery 分区现在安装此镜像，重新进入 Recovery 可启动 RAM Linux；
正常开机仍使用原厂 BOOT 启动 Android。要返回 Android，可长按
「电源＋音量下」到黑屏，松开所有按键等待启动。RAM 中修改的文件不会
随重启保存，图形界面与完整发行版尚未配置。主线 6.2 尚未成功启动。

## 第五次测试：Linux 迁入 BOOT 分区，RECOVERY 换为 TWRP（2026-10-24）

为 SteamOS 唯一系统准备启动布局。正常开机直接进入 Linux，Android 不再启动；
数据分区未清除。

| 分区 | 写入内容 | 读回 SHA-256 |
| --- | --- | --- |
| BOOT | systemd 版 4.19 内核 + 原工作 initramfs + Lineage DTB | `c5cb52f35cb1413def6646f9a1979aef4e1280f2b20c376bf4591b7792758f43` |
| DTBO | Lineage 20261011 官方 `dtbo.img` | `9383ef37364107e8de37ac0c1b12fe2b502cfc2d53cf7b68bc780ec831bb81c9` |
| RECOVERY | MinsOS 发布的 TWRP 3.7.1_12-0 gts7lwifi（第三方） | `68214dc9cedf5b2fe4c5b01800fb0411552c8a401c052ee093ad0bb8c64a5e46` |

- BOOT 镜像由 `repack-vendor-kernel.py --target boot --config-policy steamos-systemd`
  生成：头部与 Lineage `boot.img` 逐项一致，只多出原工作命令行参数；不内嵌 DTBO。
  Lineage `dtbo.img` 的 DT 表与工作 Recovery 内嵌 DTBO 逐字节相同（7 项）。
- 真机：正常开机运行配置 `c4ca8fa1…b808`，`dtbo_idx=6`，GNOME、Wi-Fi 正常；
  重建的 H4 模块加载，`hci0` 就绪。
- vbmeta 未写：设备 vbmeta 已是 `flags=3`。TWRP 包附带的 vbmeta 没有使用。
- TWRP：ADB 显示 `product:twrp_gts7lwifi`，能挂载 userdata（f2fs）并看到
  `tabs7linux/ubuntu` 与其中的 SteamOS 树（SteamOS 树后来已迁到 `tabs7linux/steamos`）。**不要在 TWRP 中 Format Data**，会清除全部
  Linux/SteamOS 数据。
- 从 TWRP 回系统用 `adb shell twrp reboot`；实测 `adb reboot` 会再次进入 Recovery
  （命令行 `androidboot.boot_recovery=1`，misc BCB 为空）。
- 从原生 Linux 可用 `reboot(RESTART2, "download")` 或 `"recovery"` 直接进入
  Download/TWRP，刷写无需按键。samloader 在非终端输出时不显示逐分区进度，
  以分区读回哈希为准。
- 回退：同次启动的原厂 BOOT/DTBO 与原 Linux RECOVERY 读回位于
  `out/steamos/native-preflash2/`（BOOT `f555ff2b…`、DTBO `790870ac…`、
  RECOVERY `7b5ec545…`），可用 samloader 写回。

## SteamOS-only initramfs 真机结果（2026-10-24）

`start-persistent-gui` 去掉 Ubuntu 出口与 `boot-target` 选择后，BOOT 由
[preflight-steamos-boot.py](../../scripts/build/vendor/preflight-steamos-boot.py)
一条命令生成：同一次启动的四分区快照（`out/steamos/native-preflash3`）、
DTBO/RECOVERY 布局、实时 DTB、`board-id [8,7]`/`msm-id [356,131073]`、已部署
BOOT（`f65cde70…`）的 AVB 签名、header v2、从该 BOOT 解出的 ramdisk 逐项比对
（只有 `init` 与 `sbin/start-persistent-gui` 变化，其余 47 项相同）。

- 写入：Download mode 由设备上的 `/usr/local/bin/tabs7-reboot-download`
  （`tools/device/reboot-download.c` 在 SteamOS 内用 gcc 编译）触发，
  `samloader flash --partition BOOT` 写 71,303,168 字节，一次成功。
- 分区读回（SteamOS 内 `sha256sum /dev/sda22`）：
  **`0eb66dea364d6da6de049123c1974f3d931e53da07736a6c322f64f48884dba8`**，与生成的
  `out/steamos/boot-preflight-20261024-143134/boot/boot.img` 一致；DTBO
  `9383ef37…`、RECOVERY `68214dc9…`、VBMETA `c92f5f6a…` 未变。
- 启动日志（`/var/log/tabs7linux-initramfs/steamos-bootstrap.log`）：
  `Verified /dev/sda35: PARTNAME=userdata, F2FS, UUID=…` →
  `Adopted the SteamOS tree from tabs7linux/ubuntu/steamos` →
  `Validated SteamOS tree: /mnt/native-data/tabs7linux/steamos` →
  `SteamOS root prepared at /newroot`。旧路径的树由一次原子重命名搬到
  `tabs7linux/steamos`，由新 initramfs 自己校验后才 `switch_root`。
- 启动后：PID 1 为 systemd，主机名 `frame`，SteamOS 0.5.3；`dbus-broker`、
  NetworkManager、seatd、logind、bluetooth 运行，`tabs7-gamescope-session.service`
  为 enabled/active，`/etc/tabs7linux/sm-t870-split` 为 `65,86`。
  `systemctl --failed` 只有 `proc-sys-fs-binfmt_misc.mount`（4.19 无 binfmt_misc，
  与本改动无关）。
- 回退：本次写入前的 BOOT 已在本机保存为
  `out/steamos/steamos-selector-boot/boot.img`（`f65cde70…`，与此前设备读回一致），
  需要时从 Download mode 写回；写入前的四分区快照在 `out/steamos/native-preflash3/`。

### userdata 探测兼容 6.2（2026-10-24，第二次写入）

`start-persistent-gui` 改为先读 GPT `partlabel`、再回退 `uevent PARTNAME` 后重新
生成 initramfs 并写入 BOOT：

- 写入前 BOOT 读回 `0eb66dea…84dba8`；`preflight-steamos-boot.py` 逐项比对显示只有
  `sbin/start-persistent-gui` 变化，其余 48 项一致。
- 写入：`samloader flash --partition BOOT`，本机镜像
  `out/steamos/boot-preflight-20261024-145309/boot/boot.img`（`37f5aa13…c4d9741995`）。
- 读回（SteamOS 内 `sha256sum /dev/sda22`）：`37f5aa13…`，与写入镜像一致；
  DTBO/RECOVERY/VBMETA 未变。
- 启动日志：`Verified /dev/sda35: userdata matched by PARTNAME, F2FS, UUID=…` →
  `Validated SteamOS tree: /mnt/native-data/tabs7linux/steamos`（没有 `Adopted`，
  因为树在上一次启动已经迁移）。PID 1 为 systemd，`tabs7-gamescope-session.service`
  active。
- 4.19 厂商内核没有暴露 `partlabel`，所以这次实际走的是回退分支；`partlabel` 分支
  要等主线 6.2 首次启动才算真机验证。
- 回退镜像：`out/steamos/boot-preflight-20261024-143134/boot/boot.img`（`0eb66dea…`）。
