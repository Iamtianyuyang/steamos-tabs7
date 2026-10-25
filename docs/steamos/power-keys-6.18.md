# 主线 6.18 物理按键与睡眠

## 硬件映射（厂商 DTS）

| 键 | 来源 | 6.18 输入设备 | 键码 |
| --- | --- | --- | --- |
| 电源 | PM8150 PON KPDPWR（`pm8150.dtsi qcom,pon_1`） | `pm8941_pwrkey` | 116 KEY_POWER |
| 音量减 | PM8150 PON RESIN（`qcom,pon_2`） | `pm8941_resin` | 114 KEY_VOLUMEDOWN |
| 音量加 | PM8150L GPIO3，低有效、上拉（`input_gts7l_common.dtsi vol_up`） | `gpio-keys` | 115 KEY_VOLUMEUP |

原 DTS 的 `gpio-keys` 处于禁用状态，并把 GPIO3 误标为音量减。现改为音量加，按厂商
`key_vol_up_default` 配置 pinctrl（normal、input、pull-up、power-source 0），并设为唤醒源。

## SteamOS 电源键语义

- `steamos-powerbuttond` 只打开带 `STEAMOS_POWER_BUTTON=1` 的输入设备，再以
  `steam -ifrunning steam://shortpowerpress|longpowerpress` 交给 Steam。
  `69-tabs7-power-button.rules` 为 `pm8941_pwrkey` 设置该属性，并排在镜像的
  `70-steamos-power-button.rules` 之前。
- 镜像是 Steam Frame 变体，原用户单元 `Requisite=steamvr.service`，并用 `vrdevice_path`
  做 ExecCondition。依赖无法用 drop-in 去掉，因此 `/etc/systemd/user/steamos-powerbuttond.service`
  整体覆盖，保留原命令，加入 `default.target.wants`。
- logind 已有 `HandlePowerKey=ignore`，电源键不会被 logind 直接关机。
- 内核原来没有 `CONFIG_SUSPEND`（`/sys/power/state` 为空）。`steamos-6.18.config` 增加
  `SUSPEND` 与 `PM_DEBUG`（提供 `pm_test`）。

### 失败记录与修正

首次在 #52 上短按电源键：powerbuttond 转交成功，Steam 记录 `Issuing Suspend Command`
并把画面淡出为黑色，随后 `systemctl suspend` 返回
`org.freedesktop.DBus.Error.InteractiveAuthorizationRequired`，内核未进入睡眠。Steam 停在
黑色画面且背光保持，用户无法恢复；重启 gamescope 会话后画面恢复。原因是 Game Mode 单元的
PAM 会话为 logind `Class=background`、无 seat、无 TTY（DRM / 输入由 seatd 提供），
`allow_active` 不适用。`50-tabs7-steamos-suspend.rules` 只为 `steamos` 用户授予
`login1.suspend` 与 `suspend-multiple-sessions`；关机和重启仍需认证，因为 PID namespace 中
systemd 的关机行为尚未核实。`pkcheck` 对 Steam 进程：suspend 为 yes，power-off 仍为 no。
把会话改成 seat0 上的正式用户会话是另一种做法，涉及 VT 与 seatd 的交接，尚未进行。

## 真机记录（2026-10-25，6.18.58 #52）

BOOT `b7de1822f00330b0cf43ae8230d619eacfaae208ebe4e3c71cf084d05485f225`，完整读回一致，
DTBO / VBMETA 未变。udev 规则、用户单元与 polkit 规则通过 USB 部署到 SteamOS 树，
内容与仓库一致；安装脚本已同步加入。

- `gpio-keys` 注册为 event3。记录脚本读到 event3 / event1 / event0 分别为 115 / 114 / 116
  的按下与抬起；powerbuttond（UID 1000）打开 event0。
- `pm_test=devices`：`PM: suspend entry (s2idle)`，等待 5 秒后退出，rc=0，无错误。
- 完整 s2idle：637.97 s 进入，663.51 s 由音量加唤醒（`pm_wakeup_irq` 171），用户确认画面正常。
- Steam 路径：短按电源键 → logind `suspend` → 再按电源键唤醒（`pm_wakeup_irq` 129）。
  powerbuttond 记录 `skipping power button push since we are already suspending or still
  actively resuming`，logind `Operation 'suspend' finished`；用户确认睡眠与唤醒正常。
  `suspend_stats` 成功 3 次、失败 0 次。

原始记录在 `out/display-recovery/20261025/keys/`。

## 仍待

- 音量键：事件已送达 gamescope，但内核未启用声音子系统（无 `CONFIG_SND`，扬声器按用户
  要求暂缓），Steam 没有可调的音量，因此没有提示也没有效果。
- 长按电源键的 Steam 电源菜单，以及菜单中的关机 / 重启（polkit 未授权，命名空间关机未验证）。
- 睡眠的长期稳定性、功耗、多次循环，以及唤醒后的触摸、USB 网络与 Wi-Fi 恢复。
- Steam 的空闲自动睡眠现在会真正生效，需按上面的路径回归。
