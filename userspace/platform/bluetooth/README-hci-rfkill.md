# GNOME 蓝牙开关与 HCI 软件屏蔽

GNOME Control Center 46.7 在切换 Rfkill 属性后又请求 BlueZ
`Powered=true`。只用 BlueZ 关闭控制器会被这次请求重新开启。
`gnome_rfkill_bridge.py` 现在先设置真实 HCI 软件屏蔽，再确认 BlueZ
关闭；开启时先解除软件屏蔽，再开启 BlueZ。属性读取来自 sysfs 和
BlueZ/NM，没有保存一个与硬件无关的“已关闭”变量。

`hci_rfkill.py` 是固定路径的 root helper，以 `/usr/bin/python3 -I`
运行，只接受 `0` 或 `1`。它要求 `hci0` 是本机 `ttyHS0` 下的控制器，
找到唯一 `name=hci0`、`type=bluetooth` 的 rfkill 子节点，使用
`O_NOFOLLOW` 和目录 FD 只读写 `soft`，随后重新读取并核对设备身份。
不固定 `rfkill2` 的索引，也不写 `/dev/rfkill`、`bt_power`、Wi-Fi、
`hard` 或 UART。阻止 HCI 后，Control Center 的自动开启请求应由
内核/BlueZ 返回 blocked。

权限仅由 `org.tabs7linux.hci-rfkill` 一个 Polkit action 授予本机
`linux` 用户。其 exec.path 固定为
`/usr/local/lib/tabs7linux/gui/hci_rfkill.py`，没有给 Python、桌面或
任何通用命令增加 capability。`userspace/platform/install-hci-rfkill.sh` 检查目标目录
属于 root 且不可由其他用户写入，安装 root-owned helper、action 和
rule；它不操作控制器。GNOME 和蓝牙增量安装器均调用它。

事务保留 BlueZ/NM 的 unique-owner pin，特权写前后核对 rfkill 设备
身份。关闭时 HCI block 可能已经改变 Powered，即使没有显式 BlueZ
Set，也会记录原值；失败回滚先恢复 soft，再恢复 Powered。替换过的
服务或 HCI 设备不接受旧事务的恢复写入。BlueZ Busy/InProgress 只做
有次数上限的重试，未知状态和失败不会发布成关闭成功。

每秒轮询仍读取真实状态，不缓存 adapter 清单或电源值。查询 NM
设备是否为 Wi-Fi/WWAN 时只读取 `DeviceType` 单属性，检查其原始
D-Bus variant 必须为 uint32；不再解包每个设备的全部 IP、连接等
属性。设备枚举、service owner、射频状态与事务校验保持实时读取。

Host 验证使用临时私有 D-Bus 与模拟 sysfs，不使用 host system bus
或真实射频设备：

```sh
/usr/bin/python3 -m unittest discover -s tests/hardware/bluetooth -p test_hci_rfkill.py -v
/usr/bin/python3 -m unittest discover -s tests/hardware/bluetooth -v
sh -n userspace/platform/install-hci-rfkill.sh userspace/platform/install-bluetooth.sh
```

测试覆盖关闭后 Control Center 自动重新开机的拒绝、重新开启、已应用
但超时的写入与回滚、owner/device 替换、真实属性类型和 unavailable、
特权命令的固定参数，以及错 UART、歧义节点、soft 符号链接和 board/
Wi-Fi 写隔离。Host 测试通过不代表实机 GNOME 开关已经验收。

部署前应只读核对实机 sysfs resolve 路径与本机 `HCI_DEVICE` 完全一致，
再以 GNOME 用户测试固定 Polkit action。实机验收应确认：关闭后开关
保持关闭、BlueZ Powered=false、hci0 soft=1；开启后恢复 Powered=true
且 soft=0；board/Wi-Fi soft、UART owner PID 不变。还需要确认重启后的
私有 GNOME bus 正常重新导出 API，Wi-Fi 仍在线。

2026-10-15 的实机验证已通过：UID1000 通过固定 Polkit action 完成 off/on；
关闭后等待 5 秒仍为 `soft=1`、`Powered=false`，设置页的强制开启请求返回
`org.bluez.Error.Blocked`；解除屏蔽后恢复开启。board/Wi-Fi 的 soft 状态和
hci0 实例身份保持不变，未重启 H4 UART 服务。用户也确认设置页关闭后保持关闭，
能够重新开启。

最终源码经过冷启动 `4100d7a9-da41-4987-b7e0-3df615af32cb` 再次通过 API 关开测试，
GNOME 私有 API、受管理蓝牙服务和手机配对记录均自动恢复，Wi-Fi 自动联网并通过
绑定 wlan0 的 TLS/HTTPS 200 检查。证据在本机 ignored
`out/device/native-gnome/flicker-20261015/cold-final/`。保留配对记录不表示手机当前
已连接，也不代表耳机音频或高速 UART 已验证。
