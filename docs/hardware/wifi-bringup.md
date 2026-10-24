# SM-T870 原生 Linux Wi-Fi bring-up

此处记录运行官方 Lineage **4.19.325-cip136-st20-perf-g4478511c4b37** 内核、BusyBox PID 1 和持久 Ubuntu 24.04 用户空间时的 Wi-Fi 初始化。它不是 mainline Wi-Fi 移植；匹配的 CNSS 后台程序使用独立 Bionic runtime，不启动 Android 系统。源码版本为 `LineageOS/android_kernel_samsung_sm8250` 的 `4478511c4b3738ad117e674810c2dfde154724bf`。

## 当前验证结果

- 已确认 QCA6390 PCI 设备 `17cb:1101`、CNSS2 和 CLD 驱动内置；原生 `/dev/wlan` 已存在。
- 已从本机的 vendor 逻辑分区只读取得固件，复制 11 个普通文件，逐文件校验源和副本 SHA-256 一致。
- 第一次 `ON` 返回 EINVAL，没有出现可用 WLAN 接口。日志中的 AMSS 加载 ENOENT 与固件搜索路径末尾误存的换行相符；已修正该路径，并准备原生 `/lib/firmware` 后备视图。
- 正常重启并修正 firmware 路径后，第二次初始化已成功加载 AMSS，约 0.2 秒进入 MHI `MISSION_MODE`（6）；未及时启动名称服务，约 20 秒后固件启动超时，ON 返回 EINVAL。
- 已安装并启动 `qrtr-ns`，确认 MHI `IPCR` 绑定 `qcom_mhi_qrtr`；实际发现 WLFW service 69 / version 1 / instance 1（node 7、port 1），CNSS 已收到 server arriving 并连接 QMI。
- 后续仅名称服务的有序启动对照：约 5.8 秒完成 MHI mission、WLFW 发现、QMI indication register 和 host capability response；仍未出现 request_mem / FW_READY，27.6 秒固件启动超时。
- 加入匹配 CNSS 第二客户端后，已实测 request_mem、regdb/BDF、M3 和 `FW_READY`；该轮主机驱动 probe 返回 `-14`（EFAULT）。补齐原厂 IPA 初始化及 10 个匹配固件后的新冷启动，完整有序流程已通过，`wlan0` 出现并自动关联，DHCP 获取 `192.168.1.217/24`。
- IPA helper 实际观察到主固件加载、主驱动初始化与 uC PIL 复位通过；`uc_ready_observed=false` 表示没有观察到 debug ready 日志，不表示 uC 初始化已失败。
- 已实机解决 Polkit 进程身份读取失败，UID 1000 的五项 NetworkManager 操作权限返回 YES。
- NetworkManager 1.46 的零 gateway 属性与本机 vendor 4.19 路由解析不兼容，导致直连和默认路由被拒。诊断中用 GNU ip 加入等效直连路由后，NM 自动加入默认路由；绑定 `wlan0` 的 HTTPS 请求已通过证书校验并收到 HTTP 200。
- 私有 NetworkManager 已原生编译并仅安装到独立 prefix。新启动 `09d772e8-bcc6-4755-a864-624c8ec99410` 中 worker 自动 ready，私有 NM 为 PID 1337；重新通过 Wi-Fi UI 连接后，DHCP 获取 `192.168.1.152`，NM 自动建立直连和 DHCP 默认路由。**本轮没有执行任何手工 route**，绑定 `wlan0` 的 TLS/HTTPS 再次通过并收到 HTTP 200。
- 新配置已保存为 root 所有、mode `0600` 的 NetworkManager keyfile；旧 Netplan YAML 仍保留。第二次干净启动 `215f4430-4880-4d52-83e9-ad2803040388` 已自动载入 keyfile、重连并获取 `192.168.1.176`，NM 自动建立路由，绑定 `wlan0` 的 TLS/HTTPS 收到 HTTP 200。**没有 Netplan generate/reload、Wi-Fi UI 连接或手工路由操作**。

本次设备证据保存在 ignored `out/device/tablet-ux/current/`：`kernel.config`、`early-dmesg.txt`、`dmesg-wifi-failed.txt`、`dmesg-wifi-2.txt`、`wifi-once-2.json`、`qrtr-ns-late.log`、`proc-devices.txt`、`partition-labels.txt`、`super-header.bin`、`super-lp-verified-table.json` 和 `wifi-firmware.json`。这些文件记录的是设备实测；factory AP 中的映像是恢复资源，不能替代设备分区快照。

第二客户端和当前权限对照的证据在 `out/device/native-phosh/cnss-ordered/`：`dmesg.txt`、连续 `wifi-kernel.log`、`cnss-daemon.log`、`ipa-firmware.json`、`ipa-blobs.tar.gz`、`polkit-internal-drop.json`、`polkit-exec-user.json` 和 `nm-permissions-user.txt`。IPA 固件归档和 manifest 是已匹配并校验的输入；不能把它们或 `--verify-only` 当作 IPA 已在硬件启动的证据。

新的路由对照和实机 HTTPS 证据在 `out/device/native-phosh/ipa-ordered/`：`nm-route-debug.txt`、`route-control.json` 和 `wifi-https.json`。该轮启动的 `/run/network` 状态为 `ready`；IPA、关联和 DHCP 的启动状态由实机读取确认，路由与 HTTPS 对照已保存为上述 host 文件。

私有 NM 最终自动重连证据已保存在 `out/device/native-phosh/private-nm-auto/`：`network-evidence.json`、`NetworkManager.log`、`ipa-attempted.json`、`ipa-kernel.log`、`wifi-kernel.log` 和 `wifi-https.json`；同轮图形状态另见 `gpu-evidence.json`。文档只记录启动、路由及连通性结果，不收录连接密码或 keyfile/YAML 的内容。

## 驱动初始化入口

原内核配置包含 `CONFIG_QCA_CLD_WLAN=y`、`CONFIG_CNSS2=y`、`CONFIG_CNSS2_QMI=y`、`CONFIG_CNSS_QCA6390=y`、`CONFIG_SEC_CNSS2=y`、`CONFIG_SEC_SEPARATE_BDFILE=y`、`CONFIG_CFG80211=y` 和 `CONFIG_FW_LOADER=y`。所以空的 `/lib/modules` 并不表示缺少 WLAN 模块。

CLD 的 `hdd_module_init()` 首先建立控制字符设备，接口不会仅因内核启动自动出现。`wlan_hdd_state_ctrl_param_write()` 收到 `ON` 时才走 `hdd_driver_load()`。其实现无条件复制三个字节，因此受控调用使用三个字节的 `ON\n`：

```sh
# 仅在固件、路径、调试通道已检查且尚未尝试 ON 的新启动中运行一次。
printf 'ON\n' > /dev/wlan
```

已观察 `/sys/class/wlan/wlan/dev` 为 `492:0`，与原生字符设备一致；这些号码应以每次启动的 sysfs 为准。当前 `rfkill0` 是 `bt_power` / Bluetooth，不能用其 soft-block 状态判断 Wi-Fi。

原生 PID 1 没有 systemd。安装 `network-manager`、`iw` 和 `wpasupplicant` 只准备用户空间程序；需要在当前系统 D-Bus 上启动 NetworkManager，并观察其设备和激活状态。无线设备尚未出现时，桌面 UI 应显示未初始化，不应把“调用启动成功”当作关联成功。

## 从实际 vendor 分区取得匹配固件

设备原来的 `firmware_class.path` 是 `/vendor/firmware_mnt/image`。只读挂载本机 `apnhlos` 后，该目录只有其他子系统固件，没有 `qca6390/amss20.bin`；WLAN 固件实际位于 vendor 逻辑分区。

本次按 GPT `PARTNAME` 确认物理 super 为 `/dev/sda28`，读取其前 1 MiB 到 host，再验证 LP metadata。`tools/device/read-super-lp.py` 只读复制的普通文件，输出 JSON 和表文本；不会打开 `--device` 参数指向的设备，也不会创建映射或挂载分区。它校验双份 geometry、双份所选 metadata 的 SHA-256 和一致性，以及表、设备、分组、extent 边界。它拒绝输出 snapshot/update/overlay、slot suffix 或多物理设备的映射。

```sh
python3 tools/device/read-super-lp.py \
  out/device/tablet-ux/current/super-header.bin \
  --partition vendor --slot 0 \
  --device /dev/sda28 --physical-size 10171187200
```

`--physical-size` 必须来自当前设备独立读取的容量，不能只抄 metadata。本次 `/sys/class/block/sda28/size` 是 **19865600 个 512 字节扇区**，即 **10171187200 字节**，与 metadata 一致。

| 校验项 | 本次结果 |
| --- | --- |
| LP 版本、metadata slot | 10.0、0 |
| 每份 metadata 上限、slot 数 | 65536 字节、2 |
| logical block、首个可用物理扇区 | 4096 字节、2048 |
| vendor 属性、大小 | READONLY、1344380928 字节 |
| vendor extent | 2625744 扇区，从物理扇区 13666304 开始 |
| 所选 metadata SHA-256 | `85db0766808ea40c9d2660c81aaffbef3254cafa63b50b1c3db9fb26f73872be` |

本次验证后的 vendor 表为：

```text
0 2625744 linear /dev/sda28 13666304
```

以上扇区和设备名仅适用于本次快照。操作设备时先重新验证分区身份和容量，使用 `dmsetup --readonly` 创建映射，再以 ext4 的 `ro,noload` 挂载到 `/mnt/vendor-reference`；`noload` 避免 journal replay。本内核没有 devtmpfs，mapper symlink 最初不能保证目标节点存在，应核对映射的 `/sys/class/block/dm-*/dm/name`、`dev`、`ro`、`size` 后，按实际 major/minor 创建节点；不能硬编码 `253:0`。

已复制以下 vendor 相对路径到 Ubuntu 的 `/usr/lib/firmware`；Ubuntu 的 `/lib` 为 usrmerge 视图，因此 `/lib/firmware` 指向相同目录。复制时保留目录结构，将相对 native 根访问它的路径视为 `/linux/lib/firmware`。

```text
firmware/qca6390/amss20.bin
firmware/qca6390/m3.bin
firmware/qca6390/regdb.bin
firmware/qca6390/bdwlan.e05
firmware/qca6390/bdwlan.elf
firmware/qca6390/bdwlan.elf1
firmware/qca6390/bdwlan.elf2
firmware/qca6390/bdwlan.elf_old
firmware/qca6390/bdwlan.elf1_old
firmware/qca6390/bdwlan.elf2_old
firmware/wlan/qca_cld/WCNSS_qcom_cfg.ini
```

它们均经验证为普通文件，无 symlink。完整文件大小、SHA-256 和复制来源在 `wifi-firmware.json`。主固件 `qca6390/amss20.bin` 为 4389024 字节、SHA-256 `5dcbc6497bf271966bfaa8df7d5f57de374f833e9069475107e9bc644e8e3af2`。

CNSS 根据板号和天线选择 BDF 名称，不宜只凭文件名选一个通用 ath11k blob。本次保留匹配设备目录中的 BDF 变体；硬件 revision 7 不需要代码中 `system_rev < 2` 的 `_old` 分支。`regdb.bin` 加载失败可由此驱动忽略，BDF 不能据此省略。可选 `wlan/qca_cld/wlan_mac.bin` 不存在本身是代码允许的情况。

## 固件路径与首次失败的因果

AMSS、M3、BDF 和 CLD INI 最终均使用 kernel firmware API。INI 的链路是 `cfg_parse()` → `qdf_ini_parse()` → `qdf_file_read()` → `request_firmware()`，无需额外恢复 Android 的 `/vendor/etc` 文件读取路径。`wlan` 参数 `fwpath` 的 setter 只保存字符串，不负责驱动加载，也不能替代全局 firmware search directory。

在当前 RAM 根中，为 kernel 异步工作线程设置的 custom path 应是完整 native 路径：

```sh
# 此 string 参数不能附带换行。
printf '%s' /linux/lib/firmware > /sys/module/firmware_class/parameters/path
```

`param_set_copystring()` 使用 `strcpy()`，sysfs store 不裁去换行；loader 又将其原样拼成 `"%s/%s"`。第一次误用 `printf '%s\n'` 后，实际查找路径成为 `/linux/lib/firmware\n/qca6390/amss20.bin`，尽管正常目录中文件存在，仍会返回 ENOENT。getter 本身也追加换行：

| 读取参数所得字节 | 含义 |
| --- | --- |
| 末尾 `0a 0a` | 保存的路径含一个 LF，getter 又追加一个 LF |
| `/linux/lib/firmware` 后仅 `0a` | 路径无 LF，只有 getter 的终止 LF |

已记录修正前后的两种字节结果。firmware loader 还会查 `/lib/firmware` 等默认目录；CNSS 内核线程与 chroot 发起的请求根视图可能不同。本次另外将原生 `/lib/firmware` bind 到持久副本，确保默认后备路径和 custom path 都看见匹配固件。

首次 ON 约 31.2 秒后返回 EINVAL；日志明确出现 `Error loading fw_fb, ret:-2`，随后 MHI 未进入 mission、固件启动超时并进入 CNSS recovery，不能将其归类为 SSID、密码或 DHCP 故障。

## 失败后的恢复与一次验证

`/dev/wlan` 的 `OFF` 只通知 blacklist manager；它不卸载驱动、不重置 CNSS 电源，也不清除 recovery 标志。首次失败后已见状态 `0x610`（DRIVER_LOADING、DRIVER_RECOVERY、FW_BOOT_RECOVERY）和“Recovery is already in progress”。重复 ON 可能只等待前一轮 driver completion，因此此次选择**正常重启原生 Linux**，在新启动中重建正确的 firmware 路径和视图后，再触发一次 ON。

此内核 `CONFIG_CNSS2_DEBUG=n`，部分 QMI assertion 不生效，但启用 subsystem restart，Samsung SSR 的某些 startup/shutdown 失败仍可进入 panic；不能保证盲目重复触发没有影响。保留 USB 调试通道，并以这一轮日志决定下一步。

新一轮需要分别记录：AMSS/M3/BDF/INI 是否加载、MHI mission 和 CNSS FW_READY、实际 WLAN netdev、NetworkManager 设备状态、扫描、关联、IP 地址和连通性。源码中的 `cnss-daemon-support` 会增加 QMI host capability 的 client 数量；内核没有直接等待 daemon 的循环，不能因此推断固件没有此依赖。加入第二客户端后的实测已推进至 FW_READY，见下文；不需要为此修改 DT。

## MHI 成功后的 QRTR 名称发现

第二次 ON 已越过 AMSS/MHI 加载。完整早期日志记录 uptime 101.772 秒进入 mission，121.824 秒因固件启动超时进入 recovery（`0x610`），142.304 秒再次超时；`wifi-once-2.json` 记录该 ON 已返回 EINVAL。名称服务直到约 444 秒才启动，迟到的连接不是正常启动顺序的对照。

原配置与 live 配置均为 `CONFIG_QRTR=y`、`CONFIG_QRTR_MHI=y` 和 `CONFIG_QRTR_SMD=y`。匹配的 `net/qrtr/Makefile` 没有内核名称服务 `ns.o`，而 `Kconfig` 明确说明 service lookup 需要用户空间 daemon。

CNSS 的 `cnss_qmi_init()` 使用 `qmi_handle_init()` / `qmi_add_lookup()`；连接时使用 `AF_QIPCRTR`、`sockaddr_qrtr` 和 `kernel_connect()`，不是旧的 MSM IPC Router 控制服务。`qmi_send_new_lookup()` 将查询发到本地 `QRTR_PORT_CTRL`。没有控制 socket 时，`qrtr_local_enqueue()` 会丢弃该控制包并返回成功，因此早期 lookup 没有显式报错不能证明服务发现正常。

Ubuntu Noble 官方 `qrtr-tools` 包提供 `qrtr-ns` 和 `qrtr-lookup`。在现有 Ubuntu chroot、同一 network namespace 中启动名称服务；chroot 本身不创建 network namespace。先检查已有进程，避免重复服务。BusyBox PID 1 不会自动运行包内 systemd unit，可以将一个前台 daemon 放到受控后台并记录日志：

```sh
# Ubuntu chroot 中，先安装 qrtr-tools 并确认实际 executable 路径。
# 不传 node-id，不新建 network namespace，不重复写 ON。
qrtr-ns -f -v > /run/qrtr-ns.log 2>&1 &
qrtr-lookup
```

`-f` 保持前台、`-v` 输出 debug 日志。源码支持名称服务晚于 CNSS 初始化启动：名称服务绑定控制端口时，`__qrtr_bind()` → `qrtr_reset_ports()` 通知已有客户端 `ENETRESET`；`qmi_data_ready_work()` → `qmi_handle_net_reset()` 重建 QMI socket 并重发所有 lookup，名称服务广播 HELLO 与远端交换服务记录。因此先观察已有 ON 的 QMI 回调，不需再触发 ON。若此前已经进入 CNSS recovery / 固件超时，则应记录该状态并重新评估，而非凭这个机制保证当前尝试一定恢复。

实测 late 名称服务日志中的 `[69:101]@[7:1]` 是 packed instance `0x101`，对应 version 1 / instance 1；`qrtr-lookup` 也发现这一服务。CNSS 收到 server arriving 后 QMI connect 成功，但连接时状态为 `0x611`（loading/recovery/fw_boot_recovery 加 connected）。此记录证明名称服务路径可用，不能证明迟启动后已恢复完整初始化。

`cnss_wlfw_host_cap_send_sync()` 根据 `ENABLE_DAEMON_SUPPORT` 设置 `num_clients=2`，还声明 BDF、M3、M3 cache、calibration 状态和可用 DDR 范围。它发送一次 host capability request，再用 `qmi_txn_wait()` 等待固件 response；内核实现没有等待第二个 client 上线的循环或 completion。固件如何解释 client 数目尚未实测，不能据此把缺 FW_READY 归因于该字段或修改它。

`qrtr-ns` 是 QRTR service registry，区别于 `cnss-daemon-support` 所指的固件额外 client。名称服务先于 ON 的有序启动对照结果见下文。`dmesg-wifi-qrtr.txt` 的 ring snapshot 已从 uptime 556 秒开始，444 秒的 hostcap 事务结果不在该文件；需在 ON 前连续保存日志，不能把被覆盖的日志当作“没有 response”。

后续有序启动的连续日志保存在 `out/device/native-phosh/first-reboot/wifi-kernel.log`：5.809 秒发送 host capability（`num_clients=2`），5.812 秒正常退出 SERVER_ARRIVE handler，证明该请求已获成功 response；27.616 秒因无 FW_READY 超时。日志没有 request_mem，故尚未进入 BDF/M3 下载和正常驱动初始化的后续阶段。

这个结果排除了“正在等待 host capability response”，但**没有排除固件在确认 host capability 后等待第二个客户端**。内核只声明 client 数目，不能用“没有内核等待循环”证明固件忽略该字段。后续已核对并启动匹配 vendor 的 `cnss-daemon`，得到下面的 FW_READY 对照。

当前内核没有可用的 daemon-support module parameter / proc 控制。`cnss_init_control_params()` 在 probe 时从 DT 的 `cnss-daemon-support` 设置 quirk；写 `quirks` 的入口仅是 CNSS debugfs，而当前 `CONFIG_DEBUG_FS=n`。`fs_ready` sysfs 只触发 cold boot calibration，不能当作 daemon-support 的替代开关。

## 匹配的 CNSS 第二客户端

本机 vendor init 中的服务为 `/system/vendor/bin/cnss-daemon -n -l`。已从只读 vendor、system 和匹配 runtime/VNDK APEX 提取 daemon、linker 与其完整依赖闭包；共 24 个 AArch64 ELF、1561 个强符号导入，版本化符号静态核对无缺失。固件与闭包都保存在 ignored `out/`，不提交厂商二进制。

独立 runtime 位于 Ubuntu `/usr/local/lib/tabs7linux/cnss-android`，manifest 为 `/etc/tabs7linux/cnss-runtime.json`。`userspace/platform/prepare-cnss.py` 检查 root 所有权、普通文件、大小、SHA-256、AArch64 ELF 和 7 个受控 alias；仅绑定当前 `/dev`、`/proc`、`/sys`。其新建的 `data/vendor/wifi` 与 Android 私有 data 无关。

原生执行匹配 linker 加载 `cnss-daemon --help` 已通过；绑定 proc 后不再出现库 realpath 错误。缺少 Android linkerconfig 的提示非致命，未出现缺符号。这个帮助测试只证明可加载 daemon；实际 WLFW 进展由后续内核日志证明。

在 `cnss-ordered/wifi-kernel.log` 中，这一轮约 297.293 秒收到 request memory；随后下载 `qca6390/regdb.bin`、revision 7 选择的 `qca6390/bdwlan.elf`（57900 字节）和 M3。297.364306 秒发出 FW_READY，297.364348 秒处理该事件，297.364427 秒记录 `Failed to probe host driver, err = -14`，随后有序关闭 MHI、PCI link 和 WLAN 电源。完整 `dmesg.txt` 没有给出更细的 HDD 失败文字，因此这个 errno 还不能单独证明某个 HDD 分支。

这证明加入匹配第二客户端后，固件能够完成此前缺失的内存/BDF/M3 初始化并到达 FW_READY。它还不是 WLAN 接口、扫描或连接成功；当前失败阶段已移到主机驱动 probe。

`userspace/platform/start-network.py` 当前顺序为：**QRTR ready → `prepare-ipa.py` → 匹配 CNSS 第二客户端 → NetworkManager → 一次 `ON\n`**。`LD_LIBRARY_PATH` 只传给 foreground `cnss-daemon -n -dd` 的隔离进程，不影响 Ubuntu 桌面。日志、PID/starttime 与单次尝试标记位于 `/run/network`。桌面重启时复用服务，已有失败尝试时保留结果直到正常重启。加入 IPA 后的新冷启动已按此顺序成功产生 `wlan0`、自动关联和 DHCP 地址。

闭包校验和 loader 实测证据位于 `out/device/native-phosh/cnss-runtime/`；第二客户端的 FW_READY 证据位于 `cnss-ordered/`，后续路由与联网对照位于 `ipa-ordered/`。

## 原厂 IPA 初始化与主机 probe 的早期 EFAULT

匹配 vendor 的 `etc/init/hw/init.target.rc` 在 `post-fs` 执行 `write /dev/ipa 1`。当前 native 环境已存在 `/dev/ipa`，字符设备及 `/sys/class/ipa/ipa/dev` 均为 `245:0`，但失败的这一轮未执行该初始化步骤。

源码给出一个最早可匹配的错误分支：`__hdd_soc_probe()` 先执行 `hdd_init_qdf_ctx()`，后者调用 `cds_smmu_mem_map_setup()`。在此 KONA/IPA3 配置中 `ENABLE_SMMU_S1_TRANSLATION` 生效；若 WLAN SMMU 已开启，而 IPA 报告的 SMMU 未开启，CDS 返回失败，HDD 立即将其转换为 `-EFAULT`。它发生在创建 HDD context、解析 INI 和启动 HIF 之前。

IPA 的 `ipa3_pre_init()` 初始将所有 `s1_bypass_arr[]` 设为 true。只有 `/dev/ipa` 的 write 触发 firmware work 后，`ipa3_attach_to_smmu()` 才真正初始化各 context bank 并更新这些标志；`ipa3_get_smmu_params()` 正是根据它们报告 WLAN 的 IPA SMMU 状态。因此缺少原厂 write 步骤具有源码支持的 EFAULT 路径，与 FW_READY 后极快失败相符。随后补齐该步骤的有序冷启动已成功产生 WLAN 接口并关联，支持这个诊断；没有修改 HDD、CNSS 或 IPA 驱动。

日志中的 `ipa3_uc_reg_rdyCB ... bad parm. inout=NULL` 是 `ipa_check_hw_present()` 故意传 NULL 检测硬件支持产生的文字，不能据此判断 IPA firmware 已失败。HDD 此处传入的是 `ucfg_ipa_is_present()`，并非 INI 的 `is_enabled()`；关闭 IPA offload 配置不等价于补齐原厂初始化。

已从本机只读 apnhlos 的 `image/` 复制并逐文件核对 10 个普通文件，持久 manifest 为 `/etc/tabs7linux/ipa-firmware.json`，记录 `files[{path,size,sha256,source}]`：

| 固件组 | MDT 字节数、program headers | 已校验的 BXX | kernel 实际请求的非空 LOAD 段 |
| --- | --- | --- | --- |
| `ipa_fws` | 7020、5 | `b00` 至 `b04` | `b02`=19080、`b03`=128、`b04`=560 字节 |
| `ipa_uc` | 6860、3 | `b00` 至 `b02` | `b02`=35212 字节 |

两个 MDT 为 ELF32 little-endian、machine 164。`peripheral-loader.c` 按 program-header 的原始索引请求 `name.b%02d`；只有 `PT_LOAD`、非 hash、`memsz > 0` 且 `filesz > 0` 的段需要外部 blob。`b00` 的 metadata 和 `b01` 的 hash 副本也与对应 header 大小一致。10 个文件的 size/hash 和必需分段闭包均已在 host 检查；原生 `userspace/platform/prepare-ipa.py --verify-only` 也已完整通过。

保存的 live DT 指定 `ipa_fws`（PAS 15）和 `ipa_uc`（PAS 27），没有其他 firmware-name override。`prepare-ipa.py` 验证 native/Ubuntu firmware inode 视图、无嵌入 LF 的 custom path 和当前字符设备身份；通过 `/run/network/ipa-attempted` 排他标记，每次开机最多发出一个字节 `1`。它从 `/dev/kmsg` 尾部连续记录新日志到 `/run/network/ipa-kernel.log`，日志丢失、IPA/uC 错误或超时均失败，不自动重写。

helper 分别等待 `IPA FW loaded successfully`、`IPA driver initialization was successful.` 和 `ipa_uc ... Brought out of reset`。第三项仅证明 PIL 认证与复位通过；实际 uC ready 是单独的 `uc_loaded` 状态，目前只有 `IPADBG` 的 `IPA uC loaded` 可供观察，不能从主驱动完成推断。输出因此使用 `primary-initialized`，并单列 `uc_pil_reset` 和 `uc_ready_observed`。新冷启动实测主固件、主驱动初始化与 PIL 复位均通过，`uc_ready_observed=false`；随后 WLAN probe、关联与 DHCP 已通过，不能把未出现 debug 日志解释成 uC 未就绪。

## 持久启动与 NetworkManager 的验证边界

自启动流程保留 USB 调试通道，并按以下顺序执行。这个顺序已通过干净冷启动验证：IPA、CNSS、私有 NM 和保存的 keyfile 自动重连均成功，DHCP、自动路由及绑定 Wi-Fi 的 HTTPS 通过。

1. 挂载已验证的 Ubuntu rootfs，核对持久固件 manifest。`prepare-wifi.py` 使用 `/etc/tabs7linux/wifi-firmware.json`；manifest 包含相对路径、大小、SHA-256 和复制来源，只列固件与 INI，不存 SSID、密码或私有 MAC 数据。目录由 root 所有，普通用户不可写。
2. 对 manifest 列出的普通文件检查大小和 hash，保留 `qca6390/` 与 `wlan/qca_cld/` 结构；确认 native custom path 与 chroot 默认路径访问同一副本。写入 `firmware_class.path` 时不附带 LF。
3. 在现有 system D-Bus 上启动唯一的 system bus daemon，并在同一 network namespace 启动唯一的 `qrtr-ns`。记录 executable、PID、日志和实际存活状态；BusyBox PID 1 环境不能依靠 systemd unit 自动运行。
4. 校验 10 个匹配 IPA 文件及 MDT 分段闭包，运行 `prepare-ipa.py`，确认本轮主驱动初始化与 uC PIL 复位的实际日志；失败则不启动 WLAN。
5. 验证隔离 CNSS runtime，启动唯一的匹配 CNSS 第二客户端，再启动唯一的 NetworkManager；新启动中仅触发一次 `ON\n`，分别保存请求结果及内核日志，等待实际 FW_READY / WLAN netdev。失败时停止该轮初始化，报告当前阶段，不用 OFF/ON 循环掩盖错误。
6. 启动桌面 UI，观察 NM 扫描、激活和 IP 结果。包安装、daemon 存活、接口出现、扫描、关联和获取 IP 是不同的验证结果。

NetworkManager 可以在接口出现前运行并处理后续设备事件，但它不会替代本内核 `/dev/wlan` 的加载入口或 QRTR 名称服务。手工启动时应继续使用系统 D-Bus，避免误把 XFCE 的 session bus 当作 NetworkManager 所在的 bus。

桌面 Wi-Fi 操作由 UID 1000 用户发起，必须另行检查该用户在当前 system bus 上的 NetworkManager 授权，例如以该用户运行 `nmcli general permissions`。root 能扫描或激活不代表桌面用户已经有权限。自动启动 helper 应记录服务与驱动结果，GUI 的“已连接”必须以 NetworkManager 设备/连接状态和 IP 结果为准。

### 已解决的 Polkit 进程身份问题

此前 UID 1000 的 NM 权限全部为 unknown，`pkcheck --process` 对仍存在的当前进程返回 `Process not found`。PolicyKit1 的 D-Bus Ping 和 daemon 存活本身不能证明它能够读取请求进程身份。

实机 `polkit-internal-drop.json` 记录：进程从 root 在内部降至 UID 993 后，自己的 `fdinfo` 目录仍为 root 所有、mode `0500`，读取 `/proc/self/fdinfo/3` 返回 EACCES。`polkit-exec-user.json` 的对照在 exec 前就切换到 UID 993，目录归 UID 993，自身 pidfd 的 `Pid:` 字段可读。

Polkit 124 成功 `pidfd_open()` 后，通过自身 fdinfo 解析 numeric PID；读取失败最终产生 `Process not found`。此 4.19 内核将非 dumpable 进程的 fdinfo 所有权交给 root，而且没有 fd 目录对应的自身进程访问例外。改为按官方 service 的 `User=polkitd` 行为，在 exec 前切换到专用身份后，实际 `nm-permissions-user.txt` 中 `enable-disable-wifi`、`network-control`、`settings.modify.own`、`settings.modify.system`、`wifi.scan` 五项为 YES；其余仍按策略返回 NO 或 AUTH。没有关闭 Polkit 或放开所有权限。这组证据证明桌面账户的授权路径已修复；后续连接与联网结果由独立设备和 HTTPS 记录证明。

## DHCP 成功后的 IPv4 路由兼容问题

有序冷启动后 `wlan0` 为 UP / LOWER_UP，地址为动态 `192.168.1.217/24`、带 `noprefixroute`。DHCP 明确返回 router 和 DNS `192.168.1.1`、lease 43200 秒。连接 profile 的 `ipv4.method=auto`、`never-default=no`、`ignore-auto-routes=no`、`route-table=0`；路由没有被配置禁用。实际内核包含 `CONFIG_INET=y`、`CONFIG_IP_ADVANCED_ROUTER=y` 和 `CONFIG_IP_MULTIPLE_TABLES=y`，不需要用更换内核选项解决这个单网关路由问题。

发行版 `network-manager` 和 `libnm0` 均为 `1.46.0-1ubuntu2.8` arm64。其 `_nl_msg_new_route()` 对 IPv4 路由无条件添加 `RTA_GATEWAY`，直连路由的值也会是 `0.0.0.0`。本机 vendor 4.19 的 `rtm_to_fib_config()` 只要见到此属性便设置 `fc_gw_family=AF_INET`，后续 `fib_check_nh_v4_gw()` 会把零地址作为网关检查并拒绝。上游 v6.2 仅在 `fc_gw4` 非零时设置这个 family。

`nm-route-debug.txt` 记录 NM 添加 `192.168.1.0/24`、metric 600、preferred source `192.168.1.217` 时返回 errno 101，extack 为 `Nexthop has invalid gateway`；缺少直连路由之后，`default via 192.168.1.1` 也被拒。因为地址带 `noprefixroute`，内核不会另行自动补上该直连路由。

受控 A/B 对照用 GNU ip 添加字段相同、但没有零 `RTA_GATEWAY` 属性的直连路由，添加成功。NM 随后自行重试并加入 `default via 192.168.1.1 metric 600`。`wifi-https.json` 记录连接 socket 用 `SO_BINDTODEVICE=wlan0`，实际本机 IPv4 为 `192.168.1.217`，访问 `https://ports.ubuntu.com/ubuntu-ports/` 完成 TLS 证书校验并收到 `HTTP/1.1 200 OK`。这证明该轮 Wi-Fi 数据通路已经可用，仍包含一次诊断手工直连路由。

私有修正见 [构建说明](../../userspace/platform/network/README-networkmanager-private.md)。它使用 Ubuntu archive 签名链验证的 matching source，保留 Ubuntu patches，只在 IPv4 `RTA_GATEWAY` 添加处增加非零判断。内核、IPv6 和非零 gateway 路径保留原实现。原生构建已通过，payload 为 4884756 字节，SHA-256 为 `ba152560d172fd534bf5144b3167d3cd2e0c270df78e3163650d214427fe60ca`；安装只包含 `/usr/local/lib/tabs7linux/networkmanager-1.46`，没有部署客户端 `libnm.so`。

`prepare-networkmanager.py` 要求 root 保护的固定 prefix、精确版本与补丁源码 SHA、manifest 的全部 size/hash，daemon 和 Wi-Fi plugin 必须是 AArch64 ELF64；额外 `libnm.so`、目录逃逸 symlink 或未记录文件均失败。`start-network.py` 在 `/etc/tabs7linux/networkmanager-private-enabled` 存在时调用该校验，并把返回的 daemon 路径记入状态；校验失败不回退其他 daemon。服务复用需要 root 所有的精确 argv 与 PID starttime，隔离 CNSS 还核对进程 root inode。既有 distro `libnm`、`/etc/NetworkManager`、`/var/lib/NetworkManager` 与 `/var/run/NetworkManager` 路径继续使用。

### 私有 NM 的新启动验证与连接配置

新启动 `09d772e8-bcc6-4755-a864-624c8ec99410` 中，worker 自动报告 ready，实际使用私有 NM（PID 1337）。这一轮没有添加任何手工路由。通过 Wi-Fi UI 重新连接后，DHCP 地址为 `192.168.1.152`；NM 自动安装 `192.168.1.0/24` 直连路由和 `default via 192.168.1.1`，默认路由为 DHCP protocol、metric 600。使用 `SO_BINDTODEVICE=wlan0` 的请求再次完成 TLS 证书校验并收到 HTTP 200。因此无手工路由的新启动内 DHCP、自动路由与 Wi-Fi HTTPS 验证已经通过。

首次切换私有 NM 时没有直接重连旧配置：原来的 profile 位于 `/etc/netplan/90-NM-*.yaml`，私有构建关闭了 Netplan 集成，因而不会自动按该格式载入 profile。旧 YAML 文件仍保留，不能将其描述为配置丢失或被删除。首次切换时运行过 Netplan generate/reload，再由用户在 Wi-Fi UI 重新连接；新 profile 已保存到 `/etc/NetworkManager/system-connections/` 的 keyfile，root 所有、mode `0600`。私有 NM 保留 keyfile backend，后续启动直接使用这个标准存储位置，不需要每次运行 Netplan 或重新在 UI 连接；文档不读取或公布其中的秘密。

**最终自动重连验收已通过。** 第二次干净启动 `215f4430-4880-4d52-83e9-ad2803040388` 中，worker 自动完成 IPA `primary-initialized`、匹配 CNSS、私有 NM（PID 1298）和保存 keyfile 的自动连接。没有 Netplan generate/reload、Wi-Fi UI 连接或手工路由操作。DHCP 获取 `192.168.1.176`，NM 自动建立 `192.168.1.0/24` 直连路由和 `default via 192.168.1.1 proto dhcp metric 600`；USB 的 `192.168.77.0/24` 路由仍存在。`wifi-https.json` 确认 `SO_BINDTODEVICE=wlan0` 请求的本机地址为 `192.168.1.176`，TLS 证书验证通过并收到 `HTTP/1.1 200 OK`。完整启动、私有 daemon 与库映射、路由及连通性证据保存在 `private-nm-auto/`。

## Telnet 监听和镜像预检

引入 Wi-Fi 前需要核对正在运行的 Telnet 服务。当前两个源码 init 和当前 host 镜像内 `/init` 均是：

```sh
telnetd -b 192.168.77.2 -l /bin/sh
```

BusyBox 1.36.1 的 `-b ADDR[:PORT]` 是 socket 绑定地址；该设置让服务监听 USB 地址。它不是接口级过滤或认证，应以实际进程参数和监听 socket 验证运行状态。若发现 wildcard listener，先区分旧镜像和额外手工启动的 telnetd；对已含此命令的源码重复构建不会改变该行为。

本次核验的 `out/vendor-linux/recovery.img` SHA-256 为 `e312cfdba7a87008222bc61f600d49905becfb32991aa11f1b7fdae79a2808ba`；其内 `/init` 与当前 `boot/initramfs/init-vendor-linux` 一致，SHA-256 为 `6cb51a3ff2242bbe17ed4ea33d63dd9401788cd852000fe55801a488eb1c641e`。这些是 host 产物证据，不能替代 live `/init` 和进程检查。

如确需更新 init，保持现有构建选项：

```sh
python3 scripts/build/vendor/build-vendor-linux.py --with-persistent-gui --standard-drm-client
```

builder 只离线产出映像，保留官方 v2 header / 4096 页设置，拆包后逐字节比较 kernel、DTB 和 recovery DTBO，检查 RAM initramfs、AVB public key、签名、rollback 和容量。当前参考组件为：

| 组件 | 字节数 | SHA-256 |
| --- | --- | --- |
| kernel | 55648280 | `af12ea22c26424cd67c0917cb5d180c1a62036321da660a65465a18ff47c6c15` |
| DTB | 1415367 | `740a196c7e9907d0bf0f13943355064548ab4cc08c9fa0519b8930ca9db837da` |
| recovery DTBO | 2367488 | `852a13be138e8cea138c77f25446c9037246d608c2bb311f1402477b20d2abad` |

重打包时以新 manifest 再次确认三个组件和 header 不变，保留 `rdinit=/init printk.devkmsg=on msm_drm.sdecustom=0`，以及 SHA256_RSA4096、rollback `1790812800`、location 0、flags 0 和 RECOVERY 容量 `86888448`。检查新的 init 内容后，再由负责设备操作的流程完成实时预检；本工具和文档不执行刷写。

## 官方源码依据

- [匹配的 Samsung/Lineage 内核](https://github.com/LineageOS/android_kernel_samsung_sm8250/tree/4478511c4b3738ad117e674810c2dfde154724bf)：`drivers/staging/qcacld-3.0/core/hdd/src/wlan_hdd_main.c` 的控制字符设备、driver load、fwpath；`drivers/net/wireless/cnss2/{pci,qmi,main}.c` 的加载、BDF 命名、超时与 recovery。
- 同一内核的 `drivers/staging/qca-wifi-host-cmn/qdf/linux/src/qdf_file.c`（INI firmware API）、`kernel/params.c`（string setter/getter）、`drivers/base/firmware_loader/main.c`（目录拼接）、`drivers/soc/qcom/subsystem_restart.c`（Samsung SSR）。源码 checkout 位于 ignored `out/vendor-kernel-prep/src/`。
- 同一内核的 `net/qrtr/{Kconfig,Makefile,qrtr.c,mhi.c}` 和 `drivers/soc/qcom/qmi_interface.c`；[QRTR 用户空间参考](https://github.com/linux-msm/qrtr)、[Ubuntu Noble qrtr-tools](https://packages.ubuntu.com/noble/qrtr-tools)与该页面提供的 `qrtr_1.0.orig.tar.gz` 中 `src/ns.c` / `src/lookup.c`。
- 同一内核的 `drivers/staging/qcacld-3.0/core/hdd/src/wlan_hdd_driver_ops.c`、`core/cds/src/cds_api.c`，以及 `drivers/platform/msm/ipa/ipa_v3/{ipa.c,ipa_uc.c}`：早期 SMMU 检查、原厂 write 入口、firmware work、主驱动与 uC ready；`drivers/soc/qcom/peripheral-loader.c`：ELF32 分段加载及 PIL 认证复位日志。
- [Polkit 124 进程身份实现](https://github.com/polkit-org/polkit/blob/124/src/polkit/polkitunixprocess.c)、[daemon 身份切换](https://github.com/polkit-org/polkit/blob/124/src/polkitbackend/polkitd.c)与[官方 service](https://github.com/polkit-org/polkit/blob/124/data/polkit.service.in)；匹配内核 `fs/proc/{base,fd}.c` 的 dumpable ownership 和 fdinfo 权限。
- [Ubuntu 官方 source pool](https://ports.ubuntu.com/ubuntu-ports/pool/main/n/network-manager/)、[NM 1.46 路由 message builder](https://github.com/NetworkManager/NetworkManager/blob/1.46.0/src/libnm-platform/nm-linux-platform.c)；匹配 vendor 内核的 `net/ipv4/{fib_frontend,fib_semantics}.c` 与[上游非零 gateway 判断](https://github.com/torvalds/linux/blob/v6.2/net/ipv4/fib_frontend.c)。具体 Ubuntu source 认证及原生构建步骤见私有 NM 构建说明。
- [AOSP LP 格式](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/include/liblp/metadata_format.h)与 [liblp reader](https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/reader.cpp)。此项目的解析器采用保守子集，格式有效的其他设备仍可能被拒绝。
- [BusyBox 官方手册](https://busybox.net/downloads/BusyBox.html)与 [BusyBox 1.36.1 源码归档](https://busybox.net/downloads/busybox-1.36.1.tar.bz2)中的 `networking/telnetd.c`：`-b` 传入 socket bind 地址。
