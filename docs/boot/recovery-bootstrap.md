# 为 SM-T870 获取 root Recovery

设备为 `SM-T870 / gts7lwifi`、硬件版本 7，已解锁 Bootloader，此前运行
`T870XXS8DXH1` Android 13。原厂 Recovery 无法提供所需的 root ADB。
修改前 Android 报告 `ro.boot.veritymode=enforcing`；Bootloader 已解锁不代表
AVB 校验已经关闭。

## 已准备的文件

文件位于项目的 `out/`。官方 Recovery 已安装并成功启动，已取得 root ADB。

| 用途 | 文件 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| root Recovery 候选 | `out/recovery-reference/lineage/recovery.img` | 86,888,448 字节 | `54ae2d34a9bfdb30915ae08e07d2317197afc0087c492f0b763042e100ff702f` |
| 关闭 AVB | `out/recovery-reference/lineage/vbmeta.img` | 8,192 字节 | `63d13f4d294a1228a3e88e1a6c9d8a522f63f88b5f2c0181114c2a4718385e3d` |
| 刷机工具 | `out/tools/samloader-2.2.0/samloader` | Linux x86-64 | `82a4692364aa185c9f3a68678bca5979f87b7ba3de3f02fceed6a0c83f336aaf` |

两个镜像来自 [SM-T870 官方构建 API](https://download.lineageos.org/api/v2/devices/gts7lwifi/builds)
的 2026-10-11 构建，已核对大小与 SHA-256。工具来自
[samloader-rs 2.2.0](https://github.com/topjohnwu/samloader-rs/releases/tag/2.2.0)，
下载归档也已核对 GitHub 发布的 SHA-256。版本和预期哈希固定在
`hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json`。

同版本原厂 `boot/recovery/dtbo/vbmeta` 镜像已经提取并校验，保存在
`out/stock-firmware/T870XXS8DXH1/`。它们来自原厂固件包，不能称作设备分区备份。
现已在 `out/device/lineage-recovery/` 取得完整的 boot/dtbo/recovery/vbmeta
设备转储。boot 与 dtbo 与原厂包一致；recovery 与新安装的官方镜像一致。

已在 Download mode 读取真机 PIT，过程未写入分区，设备随后自动返回 Android。
`out/recovery-reference/device.pit` 的 SHA-256 为
`1d6f97aa7f5813fc0b97df4e242a27b9f5bc3a081534c66524de87af6e37ad48`。
PIT 的分区容量与 Android `/proc/partitions` 对照一致：

| 分区 | PIT 编号 | 真机容量 | 候选镜像大小 |
| --- | ---: | ---: | ---: |
| RECOVERY | 23 | 86,888,448 字节 | 86,888,448 字节 |
| VBMETA | 66 | 65,536 字节 | 8,192 字节 |

完整的 BOOT/DTBO/RECOVERY/VBMETA 容量记录在上述 profile 中。PIT 表项的
每块 4096 字节由原厂镜像大小和 Android 分区大小共同核对，不从节点名猜测。

## 必须先决定的数据清空

[LineageOS 三星高通预安装流程](https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_includes/templates/device_specific/before_recovery_install_samsung_qcom.md)
说明：刷入该 `vbmeta.img` 后设备会要求恢复出厂设置。它的 AVB 标志为 `3`，
原厂包中为 `0`。按该流程推进，必须接受平板内部应用、账号和文件被清空。
2026-10-13 原厂 Android 的 `/data` 显示已用约 38 GiB；这包含系统与应用数据，
不是个人文件大小统计。用户已于 2026-10-13 明确指示「直接清理继续」，
授权清空并继续；没有备份个人数据。

`VBMETA` 写入命令已于 2026-10-13 13:57 UTC 前成功退出，日志位于
`out/logs/flash-vbmeta.log`，平板随后重启并出现未授权的 Recovery ADB。
恢复出厂后的 `/data` 已用约 2.9 GiB，初始设置完成。随后安装官方 Recovery，
用户确认进入 Lineage Recovery 并在 Advanced 中启用 ADB。
root ADB 的分区读回确认 recovery 与官方文件完全一致，vbmeta 前 8,192 字节
与官方文件完全一致，AVB 标志为 3。Bootloader 仍解锁，BOOT/DTBO 转储与
原厂包一致。后续 Linux 测试及恢复结果见 [原生启动记录](native-boot-tests.md)。

恢复出厂并完成设置后，保持 Wi-Fi 联网，再检查开发者选项中的「OEM 解锁」
是否开启。这是 [官方三星高通前置流程](https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_includes/templates/device_specific/before_recovery_install_samsung_qcom.md)
要求的步骤。预检在正常 Android 的 ADB 状态下额外要求
`sys.oem_unlock_allowed=1`，不会仅凭 `flash.locked=0` 判断允许自定义镜像。
Recovery 环境会记录该属性，但不会把可能缺失的值当作 Android 开关状态。

## 操作顺序

在项目目录执行预检；脚本只检查文件和读取 Android 属性：

```bash
python3 scripts/recovery/recovery-preflight.py --device --pit
```

同意清空并完成需要的个人数据备份后，先用 `adb reboot download` 进入
Download mode。若需要重新读取 PIT，不写入分区：

```bash
out/tools/samloader-2.2.0/samloader dump-pit --output out/recovery-reference/device.pit --no-reboot
out/tools/samloader-2.2.0/samloader print-pit --file out/recovery-reference/device.pit
```

确认真机型号、解锁状态、`RECOVERY` 和 `VBMETA` 名称及容量与镜像兼容。
如果 USB 权限或协议连接失败，先解决连接；不跳过大小检查，不提交 PIT。

按官方流程先修改 AVB，然后在平板提示时恢复出厂设置：

```bash
out/tools/samloader-2.2.0/samloader flash --partition VBMETA out/recovery-reference/lineage/vbmeta.img
```

完成 Android 初始设置，联网，检查 OEM 解锁仍启用，并重新启用 USB 调试。
再次进入 Download mode，安装官方 Recovery：

```bash
out/tools/samloader-2.2.0/samloader flash --partition RECOVERY out/recovery-reference/lineage/recovery.img --no-reboot
```

[官方 Recovery 安装流程](https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_includes/templates/recovery_install_samloader_rs.md)
要求写入完成后立即进入 Recovery：按住音量下和电源约 8–10 秒，屏幕变黑
马上松开，随后按音量上和电源进入 Recovery。保持 USB 连接。若先进入原厂
Android，自定义 Recovery 可能被覆盖。这里只使用 Recovery 取得调试访问，
不安装 LineageOS ROM。

如需手动进入 Download mode，[该机型定义](https://raw.githubusercontent.com/LineageOS/lineage_wiki/main/_data/devices/gts7lwifi.yml)
指定连接 USB 时按音量上、音量下和电源，在警告页选择 Continue。
Bootloader 已解锁，不重复选择 Unlock Bootloader。

Recovery 中启用 ADB，确认 `adb shell id` 返回 UID 0，再执行：

```bash
python3 scripts/device/collect-device.py --backup --output out/device/lineage-recovery
```

脚本检查型号、固件、硬件版本和 PIT 中的完整分区容量，拒绝截断的转储。
`vbmeta-device.img` 是完整分区，大小 65,536 字节；应核对其前 8,192 字节
是否与官方 `vbmeta.img` 完全一致，不能直接比较两个不同大小文件的 SHA-256。

这时的 `boot`、`dtbo` 转储仍可记录未修改的设备分区；`recovery` 转储记录的是
刚安装的自定义 Recovery，不能称为被覆盖前的原厂 Recovery 备份。
`live.dtb` 也属于当前 Recovery 启动环境。主线 Linux 草稿须继续核对实时内存
保留区、AVB 和 DTBO 处理后，才进入首次启动测试。

## 原厂恢复资源

匹配固件已准备，可在必要时从 Download mode 恢复原厂 Recovery 和 vbmeta：

```bash
out/tools/samloader-2.2.0/samloader flash --partition RECOVERY out/stock-firmware/T870XXS8DXH1/recovery.img --partition VBMETA out/stock-firmware/T870XXS8DXH1/vbmeta.img --no-reboot
```

恢复 AVB 状态也可能要求再次恢复出厂设置。原厂镜像可以恢复相应启动组件，
无法恢复已经清空的个人数据。
