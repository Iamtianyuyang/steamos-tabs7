# SM-T870 原生蓝牙

运行环境是原生 BusyBox PID 1、Ubuntu 24.04 和厂商 Linux
4.19.325-cip136-st20-perf-g4478511c4b37。主线 6.18 没有在设备上启动。

已实测：修复后的内核正常启动；HCI socket 创建、关闭和 CONTROL
ReadIndexList 成功；匹配 H4 模块加载成功；原厂固件与 RAM NVM 完成下载，
Bluetooth HCI RESET 后 patch 从 `0x0d2b` 更新为 `0x5def`；hci0 出现，
BlueZ 5.72 可以开启控制器，10 秒扫描发现 28 个设备并正常停止。
地址与原 EFS 地址一致。用户已在手机确认配对，BlueZ 的 Device1 返回
`Paired=true`、`Connected=false`：手机配对已验收，连接和音频尚未验收。
持续待机后的蓝牙通信也没有验收。
证据保存在 ignored `out/device/native-gnome/hardware/bluetooth-first/`。

## A2DP 音频栈

配对成功后从平板连接手机曾稳定失败：
`br-connection-profile-unavailable`，且 `bluetoothctl show` 的 adapter
UUID 列表没有 A2DP Sink/Source（0000110a/0000110b）。根因不在蓝牙链路，
而在 PipeWire 音频栈：WirePlumber 0.4.17 的 stock bluez monitor
（`/usr/share/wireplumber/scripts/monitors/bluez.lua`）在 logind 插件
存在时，只有 seat state 变为 `active` 才创建 `api.bluez5.enum.dbus`
SPA monitor。本机没有 systemd-logind，`libwireplumber-module-logind.so`
加载后 seat 永远非 active，monitor 永远不被创建：PipeWire 不向 BlueZ
注册任何 `MediaEndpoint1`，BlueZ 因此没有 A2DP profile 可连，错误
表现为 connect 时 `Protocol not available`（bluetoothd.log）。全过程
无 warning 级日志，单靠日志无法发现。

修复是弃用 logind 门控：
`/etc/wireplumber/bluetooth.lua.d/60-no-logind.lua` 设置
`bluez_monitor.properties["with-logind"] = false`，monitor 改为无条件
创建。仓库文件在 `userspace/platform/wireplumber/bluetooth.lua.d/60-no-logind.lua`，
需要手工部署到 `/etc/wireplumber/bluetooth.lua.d/`；改后重启音频栈（在宿主会话的
D-Bus/SID 环境里重跑音频服务；本项目已没有 Phosh/XFCE 会话脚本）。

验收（2026-10-15，同一冷启动会话，证据在 ignored
`out/device/native-gnome/hardware/bluetooth-a2dp-logind-fix/evidence.txt`）：
adapter UUID 新增 Audio Sink/Audio Source；PipeWire 注册的 MediaEndpoint1
出现在 BlueZ；手机 `Connected: yes`，dmesg 出现
`input: 我的 (AVRCP) as /devices/virtual/input/input12`；
`wpctl status` 的 Audio Devices 出现 `我的 [bluez5]` 声卡。

限制：内核没有 CONFIG_BT_RFCOMM / CONFIG_BT_BNEP，HFP 通话
（`RFCOMM socket: Protocol not supported`）和 PAN 不可用，A2DP/AVRCP
不受影响。本机尚无 ALSA 声卡（wpctl 只有 Dummy Output），对端开始
A2DP 串流后 sink 节点才会出现，实际出声还依赖后续的扬声器通路验收。

内核问题来自厂商 hci_sock.c 的十处 `#if 0`：socket creator 假返回成功，
留下空 `sock->sk`，原 AF_BLUETOOTH 入口因此触发 BUG。修复恢复原来的
creator、bind、release、ioctl、析构和相关 helper，保留 `bt_sock_alloc`
安全修订，不改变原内核配置。补丁在
`hardware/samsung/gts7lwifi/kernel/patches/vendor-4.19/hci-socket-restore.patch`。

修复内核的 Image SHA256 是
`4c2f635e6799eb292c08f1cb065d948df2cedfd45b546481410b4c2c4a05830c`。
Image 存储大小与原件都是 55,648,280 字节；ARM64 text_offset `0x80000`、
image_size `0x4678000`、flags `0xa` 保持不变。原配置 SHA256 是
`02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117`，
CFI、LTO 和 ShadowCallStack 保留，原配置与修复构建均为
`CONFIG_SHADOW_CALL_STACK=y`。全部 13,113 个原导出符号的 CRC 保持一致。
Recovery 的其他组件、设备树、DTBO 和签名参数由独立 repack 流程保留并验证。

`bootstrap-qca6390-h4.py` 只使用经核验的 ttyHS0、ROM 和原厂 blob。
原厂 NVM 文件保持不变；RAM 副本仅更新实际 EFS 地址、115200 波特率，
以及本机 HAL 格式的 SIBS 位。初始版本必须匹配实测 ROM baseline，
避免重复下载到已经运行的 RAM 固件。下载结束后同一个 FD 交给 N_HCI/H4，
该进程继续持有 UART 时钟票。`SIGTERM` 按顺序解绑、释放时钟并恢复 UART。

这是初期功能配置：115200 8N1 的串口线速上限约 11.5 kB/s，实际数据吞吐更低；
SIBS 关闭且 UART 时钟保持开启。高速传输和节电需要后续按真实厂商协议适配，
当前不能据此声称蓝牙音频已可用。

## 部署资源

控制器是 `userspace/platform/start-bluetooth.py`，安装后由 root 在 Ubuntu 内调用。
它使用独立 SID，先等待真实 `hci-ready`，再启动 bluetoothd 并核验
`org.bluez` 的实际 PID。共享 system D-Bus 应先由原生网络启动流程建立；
蓝牙控制器不会停止或删除它，也不管理 GUI、Wi-Fi 和配对 agent。

默认文件布局：

| 路径 | 内容 |
| --- | --- |
| `/usr/local/lib/tabs7linux/gui/start-bluetooth.py` | 管理控制器 |
| `/usr/local/lib/tabs7linux/bluetooth/bootstrap-qca6390-h4.py` | 本目录 loader |
| `/usr/local/lib/tabs7linux/bluetooth/probe-ttyhs0-version.py` | 本目录 UART probe |
| `/usr/local/lib/tabs7linux/bluetooth/module/tabs7_hci_uart.ko` | 匹配的 H4 模块 |
| `/usr/local/lib/tabs7linux/bluetooth/firmware/htbtfw20.tlv` | 本机原厂 patch |
| `/usr/local/lib/tabs7linux/bluetooth/firmware/htnv20.bin` | 本机原厂 NVM |
| `/etc/tabs7linux/bluetooth/bt_addr` | 原 EFS 的 17 字节 ASCII 地址，0600 |

程序、配置和祖先目录应属于 root，并且不允许其他用户写入。
模块、固件和地址不提交 Git。模块 SHA256 必须是
`c70a7c1105789be7f3679ec149108312d94d016dd2ff5d298cb8e8e0e9a5aef8`；
已加载的模块通过导出的 GNU build-ID note 与该 ELF 核对。

可选 `/etc/tabs7linux/bluetooth/config.json` 只覆盖路径，例如使用现有 GUI
源码安装目录时：

```json
{
  "loader": "/opt/tabs7linux/userspace/platform/bluetooth/bootstrap-qca6390-h4.py",
  "probe": "/opt/tabs7linux/userspace/platform/bluetooth/probe-ttyhs0-version.py"
}
```

```sh
python3 /usr/local/lib/tabs7linux/gui/start-bluetooth.py start
python3 /usr/local/lib/tabs7linux/gui/start-bluetooth.py status
python3 /usr/local/lib/tabs7linux/gui/start-bluetooth.py stop
```

`userspace/platform/install-bluetooth.sh` 把源码部署到上述路径；它要求已验证的模块、原厂固件
和地址先就位，然后启动并检查状态。自动启动由
`/etc/tabs7linux/bluetooth/enabled` 标记控制，GUI 入口只调用独立蓝牙控制器。

日志和 ownership record 在 `/run/tabs7-bluetooth/`。重复 start 验证已有会话；
stop 先结束 bluetoothd，再结束 UART owner，最后恢复属于本次操作的资源。
错误或未完成清理会保留 record；不会按陈旧 PID 杀进程或删除共享 socket。
最初已开启的 BT 会做一次受控 BT-only ROM 初始化循环，成功后保持原开启状态；
最初关闭的 BT 在 stop 后恢复关闭。外部 BlueZ、UART 或 HCI 占用会使启动拒绝。
管理控制器的真实 start、重复 start、status、stop 和 restart 已通过；
stop 返回 `cleanup_errors=[]`，restart 后手机配对记录保留，
`Paired=true`、`Connected=false`。
独立冷启动 hook 已由主启动流程整合，`9210e0ee-baf5-4bb5-8790-1f42eb2fd593`
实机启动中自动进入 ready，手机 `Paired=true` 记录保留；同次启动 Wi-Fi 自动
连接并通过绑定 `wlan0` 的 TLS/HTTP 200。证据在 ignored
`out/device/native-gnome/hardware/final-cold-combined/` 的 `bluetooth-state.json`、
`bluetooth-hook.log`、`paired-state.json` 和 `wifi-https.json`。

冷启动还覆盖了真实 fork/exec 竞态修正：控制器等待同一个未回收子进程的
两次稳定、精确 argv 采样后才保存完整归属；过渡期间只记录 PID/start/SID/UID。
超时和 STOP 只能清理仍由 Popen 持有且出生身份相符的子进程，身份不明时保留记录。
正式归属保存后仍严格核对 argv。延迟 exec、早退、超时和旧空记录清理的独立验证
在 `out/bluetooth/research/controller-exec-race-verification.json`。

GNOME 蓝牙设置负责配对提示。`Pairable=false` 只影响远端发起的配对，
本机发起的配对还需要可响应的 Agent1。已有手机配对实测，不能扩大为所有设备
或所有 profile 均已连接。
参见 [BlueZ Adapter API](https://github.com/bluez/bluez/blob/5.72/doc/org.bluez.Adapter.rst)
和 [AgentManager API](https://github.com/bluez/bluez/blob/5.72/doc/org.bluez.AgentManager.rst)。

## 离线重建

依赖已有且经校验的 `out/vendor-kernel-prep/`、原 Recovery 的配置/组件以及恢复的
`out/references/recovered-module-abi/Module.symvers`。工具链固定为 AOSP
clang-r563880c / Android 14054515，源 commit 固定为 4478511c4b37，Docker
镜像按 digest 固定。不会下载工具或安装主机全局包。

```sh
python3 scripts/build/bluetooth/build-bluetooth.py --phase kernel --dry-run
python3 scripts/build/bluetooth/build-bluetooth.py --phase kernel
python3 scripts/build/bluetooth/build-bluetooth.py --phase h4
```

`scripts/build/bluetooth/verify-bluetooth-kernel.py --build-root <输出目录>` 也可单独重新核验
已有 Image；所用 H4 ABI 记录在 `hardware/samsung/gts7lwifi/profiles/bluetooth-h4.json`，不依赖旧手动构建日志。

每次使用独立输出目录；既有产物会被保留。原参考源只读；kernel 阶段只在副本中
应用单文件补丁，并运行 `verify-bluetooth-kernel.py` 核验嵌入配置、Image header、
CFI、恢复符号和原导出 CRC。H4 阶段实际重复编译已经与上述硬件验收模块逐字节相同。
这些脚本不访问设备、不打包 Recovery，也不刷写分区。
