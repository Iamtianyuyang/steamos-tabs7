# SM-T870 私有 NetworkManager 路由兼容构建

发行版 NetworkManager `1.46.0-1ubuntu2.8` 为 IPv4 直连路由也发送 `RTA_GATEWAY=0.0.0.0`。匹配的 Lineage 4.19 内核只要看见这个属性就选择 gateway 校验，返回 `Nexthop has invalid gateway`。NM 使用 `noprefixroute` 配置地址，所以直连路由被拒后，默认路由也无法建立。上游新内核只在 gateway 非零时设置对应 family。

实机 DEBUG 日志和 GNU ip 对照已确认：完全相同的直连路由字段，省略零 gateway 后成功；NM 随后自行添加默认路由，通过绑定 `wlan0` 的 HTTPS 请求完成 TLS 校验并收到 HTTP 200。证据在 ignored `out/device/native-phosh/ipa-ordered/{nm-route-debug.txt,route-control.json,wifi-https.json}`。这个对照用过临时手工路由；后续私有二进制的新启动已另行完成无手工路由的 DHCP、自动路由及 HTTPS 验证，见本文末尾。

`NetworkManager-1.46-zero-gateway.patch` 只在 `_nl_msg_new_route()` 添加一个非零判断，其他 IPv4 属性、非零 gateway 与 IPv6 路径保持原实现。没有修改内核、协议接口或客户端 libnm。

## 源码验证

源码为 Ubuntu `noble-updates/main` 的 matching `network-manager` source，保留 Ubuntu patch series，包括 6 GHz、SAE、自动重连和 DHCP 修正。官方 archive 的 InRelease 用 `/usr/share/keyrings/ubuntu-archive-keyring.gpg` 验证，签名指纹为 `F6ECB3762474EDA9D21B7022871920D1991BC93C`；其 SHA256 覆盖 `main/source/Sources.xz`，再由 exact-version stanza 认证下面三个输入：

| 文件 | 字节数 | SHA256 |
| --- | --- | --- |
| `network-manager_1.46.0.orig.tar.xz` | 7033296 | `722649e25362693b334371473802a729b0ec9ee283375096905f868808e74068` |
| `network-manager_1.46.0-1ubuntu2.8.debian.tar.xz` | 92028 | `f7124430e969f331e7f090e29e807e1feb5905a5acd7c27a13b46eac62cca9f8` |
| `network-manager_1.46.0-1ubuntu2.8.dsc` | 3848 | `b550d2f66ff4c1f4618d95cfb71184b909f8a40ff341cce0ebe3ea4b62109317` |

原始输入、签名 metadata 和校验结果在 ignored `out/networkmanager-private/source-verification/`。`dpkg-source --no-check -x` 只用于展开此前已由 archive 签名链逐项认证的文件；不依赖未经信任的 uploader key。准备好的 bundle 包含 `source/` 和 `verification/`，其中源码已应用 Ubuntu patches 和本地单条件补丁。传输后先核对 bundle 的独立 SHA256，再解包。

`verify-networkmanager-source.py` 可以在 host 或 native Ubuntu 离线重复签名链与三个文件校验。builder 还固定检查源码 changelog、修改前/后的 platform 文件 SHA256：

```text
matching Ubuntu: e0a2e91e2b1a942488e4bf8b3b6e72362bfb297ded82fd5eb50b66ed8001ab99
with zero-gateway guard: baa746045c1aa9ed2118d22db3be9364d16c4f1717b7e68de30b5b14ca231f2d
```

## 原生构建

在已有 native Ubuntu arm64 中准备以下 development packages。它们不是完整发行版 `apt build-dep`；既有 GPU 编译工具通常已覆盖第一行及部分开发库。APT simulation 的 package/字节结果才是本机准确依赖估算；一般剩余空间应预留约 1 GiB，默认两路编译，尚无本机完整编译耗时测量。

```text
build-essential meson ninja-build pkg-config gettext xsltproc
libglib2.0-dev uuid-dev libudev-dev libdbus-1-dev libndp-dev libgnutls28-dev
libpolkit-gobject-1-dev libsystemd-dev libcurl4-gnutls-dev libpsl-dev
```

脚本不安装 package、不操作网络接口，也不替换正在运行的 daemon。假设 bundle 解到 `/usr/src/tabs7linux-nm/`，将本目录的 patch、verifier、builder 放到同一目录后运行：

```sh
python3 verify-networkmanager-source.py /usr/src/tabs7linux-nm/verification
bash build-networkmanager-private.sh \
  /usr/src/tabs7linux-nm/source \
  /usr/src/tabs7linux-nm/build \
  /usr/src/tabs7linux-nm/stage 2
```

BUILD 和 STAGE 必须是新的、与 SOURCE 分开的目录。若编译中断，可在已配置的 BUILD 手工继续 `meson compile -C BUILD -j 2`；builder 不删除已有输出。

保留 Wi-Fi、wpa_supplicant、internal DHCP、Polkit、systemd/logind 会话、TLS crypto 和 connectivity check。关闭 CLI/TUI、Qt/GIR/Vala、docs/tests 及此平板未使用的 PPP、MM、OVS、Netplan；既有 keyfile 仍由 `/etc/NetworkManager` 管理。Meson 1.46 没有关闭 libnm 构建的选项，因此仅丢弃 staging 中的客户端 library/header/pkgconfig，不覆盖发行版 libnm。GNOME/Phosh/nmcli 继续使用原 package 的 libnm 和 D-Bus API。

编译的固定路径为：

```text
prefix: /usr/local/lib/tabs7linux/networkmanager-1.46
executable: prefix/sbin/NetworkManager
plugins: prefix/lib/NetworkManager/1.46.0-1ubuntu2.8+tabs7linux-zero-gateway
configuration: /etc/NetworkManager
state: /var/lib/NetworkManager
run: /var/run/NetworkManager (Ubuntu 的 /run 视图)
```

`meson install` 始终使用独立 DESTDIR。输出 `BUILD/networkmanager-private.tar.gz` 只包含 private prefix，附文件 SHA256 manifest；staging 中的 `/etc`、`/var`、全局 D-Bus/Polkit 配置不在部署 tar 内。不需要给桌面进程设置 `LD_LIBRARY_PATH`。部署及 root worker 的 executable 选择另行完成。

原生构建已通过，private payload 为 4884756 字节、SHA256 `ba152560d172fd534bf5144b3167d3cd2e0c270df78e3163650d214427fe60ca`，已仅安装 private prefix，确认无客户端 `libnm.so`。daemon SHA256 为 `ced2946f6566ac7979c97a788819f11d4999233e5143ca8ad28d312a0b6f3a60`，Wi-Fi plugin 为 `792835f03e9f8f6686c4a62aa0a150d8ceb32379b94e258f91e0d1acbe25bda1`。

`userspace/platform/prepare-networkmanager.py` 在 native root 校验 fixed prefix、root ownership/权限、manifest 全部 size/hash、ELF64 AArch64 和补丁来源，拒绝目录外 symlink、未记录文件和任何客户端 libnm。root worker 仅在 `/etc/tabs7linux/networkmanager-private-enabled` 存在时选择校验返回的 daemon 路径；失败时不回退其他 binary。

首次私有 NM 启动 `09d772e8-bcc6-4755-a864-624c8ec99410` 中 worker 自动 ready，私有 NM 实际为 PID 1337；没有执行手工 route。通过 Wi-Fi UI 重新连接后，DHCP 获取 `192.168.1.152`，NM 自动安装直连路由和 `default via 192.168.1.1 proto dhcp metric 600`。绑定 `wlan0` 的 HTTPS 已完成 TLS 证书校验并收到 HTTP 200。这是无手工路由的补丁对照。

旧 profile 存于 `/etc/netplan/90-NM-*.yaml`，文件仍保留；私有构建的 `netplan=false` 不自动读取此格式。首次切换时经过 Netplan generate/reload 和 Wi-Fi UI 重新连接，新 profile 已保存为 `/etc/NetworkManager/system-connections/` 中 root 所有、mode `0600` 的 keyfile。不需要删除旧 YAML；后续启动直接使用 keyfile，无需重复 Netplan generate/reload 或 UI 连接操作。

**保存 keyfile 后的冷启动自动重连也已通过。** 第二次干净启动 `215f4430-4880-4d52-83e9-ad2803040388` 自动完成 IPA、CNSS、私有 NM（PID 1298）和 keyfile 重连，没有 Netplan、UI 连接或手工 route 操作。DHCP 获取 `192.168.1.176`，NM 自动安装直连和默认路由（metric 600），绑定 `wlan0` 的 TLS/HTTPS 收到 HTTP 200。完整证据在 ignored `out/device/native-phosh/private-nm-auto/{network-evidence.json,NetworkManager.log,ipa-attempted.json,ipa-kernel.log,wifi-kernel.log,wifi-https.json}`；发行版 libnm 和 USB 调试通道继续可用。

## 冷启动接口命名

后续冷启动 `15c83ab7-c0cd-47b8-8adc-1a84e132c211` 暴露了独立的接口命名问题：
systemd 255 的 `99-default.link` 在启动约 9.3 秒把 interface index 10 的
`wlan0` 改名为 `wlp1s0`。CNSS/QCA6390 驱动和 NetworkManager 正常，
但保存的 keyfile 指定 `wlan0`，因此改名后的网卡停在 disconnected。
此前冷启动的自动重连证明路由修复有效，不能覆盖这次不同 udev 时序的问题。

`10-tabs7-wlan.link` 仅匹配原始名字 `wlan0`、驱动 `cnss_pci`；空
`NamePolicy=` 使明确的 `Name=wlan0` 生效。文件安装到
`/etc/systemd/network/10-tabs7-wlan.link`，按顺序先于 `99-default.link`。
Phosh 基础安装器和 GNOME 增量安装器均在启动 udev 和网络 worker 前部署它，
图形启动器、私有 NetworkManager、固件和现有 keyfile 无需改变。
在断开态把已有 `wlp1s0` 恢复为 `wlan0` 后，实机 `udevadm test-builtin`
确认采用本规则且 `ID_NET_NAME=wlan0`；NetworkManager 随即使用保存的 keyfile
自动尝试激活，没有手动 connection up、路由或密码操作。
这次运行中改名后的连接约 25 秒后报告 `ssid-not-found`，未完成联网；
尚未证明正在运行的 supplicant 是否保留旧接口名字。
**规则部署后的真实冷启动自动重连已通过。** 启动
`9210e0ee-baf5-4bb5-8790-1f42eb2fd593` 中，`10-tabs7-wlan.link` 正确匹配，
接口保持 `wlan0`；NetworkManager 自动使用保存的 keyfile 连接，DHCP 获取
`192.168.1.211`。绑定 `wlan0` 的 HTTPS 请求完成 TLS 校验并收到 HTTP 200，
没有手动激活、改名或路由操作。证据保存在 ignored
`out/device/native-gnome/hardware/final-cold-combined/` 的
`NetworkManager.log`、`network-evidence.json`、`wifi-https.json`、
`wlan-udev.info` 和 `validation-summary.json`。

已经被 userspace 改名的 `wlp1s0` 不再匹配 `OriginalName=wlan0`；这个规则
预防新的冷启动改名，不会自行重命名活动接口。实机验证应先在断开态恢复
`wlan0`，再检查 `udevadm test-builtin net_setup_link /sys/class/net/wlan0`
的 `ID_NET_LINK_FILE` 和 `ID_NET_NAME`，然后冷启动验证保存的 keyfile 自动连接
及绑定 `wlan0` 的 TLS/HTTPS。仅 `NetworkManager` 进程 ready 不能证明联网。

语义见 [systemd v255 systemd.link](https://github.com/systemd/systemd/blob/v255/man/systemd.link.xml)
和 [v255 link-config.c](https://github.com/systemd/systemd/blob/v255/src/udev/net/link-config.c)。

## 一手来源

- [Ubuntu official InRelease](https://ports.ubuntu.com/ubuntu-ports/dists/noble-updates/InRelease)、[matching source pool](https://ports.ubuntu.com/ubuntu-ports/pool/main/n/network-manager/)；exact 来源以已验证的 Sources stanza 为准。
- [NM 1.46 route message builder](https://github.com/NetworkManager/NetworkManager/blob/1.46.0/src/libnm-platform/nm-linux-platform.c)；部署所用文件以 Ubuntu 2.8 source 为准。
- [matching vendor kernel route parser](https://github.com/LineageOS/android_kernel_samsung_sm8250/blob/4478511c4b3738ad117e674810c2dfde154724bf/net/ipv4/fib_frontend.c)及 `fib_semantics.c`；[upstream 非零 guard](https://github.com/torvalds/linux/blob/v6.2/net/ipv4/fib_frontend.c#L776)。
