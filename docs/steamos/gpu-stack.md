# SM-T870 原生 GPU 渲染

SM-T870 已在原生 Ubuntu 24.04、Lineage/Samsung 4.19.325 内核上使用 Adreno 650 v3 加速 Phoc 合成。路径为私有 Mesa 25.2.8 Turnip KGSL → Vulkan → Gallium Zink → EGL/GLES/GBM，继续由已有双 VIG wlroots 显示。用户已确认桌面明显更流畅，完整显示与触摸正常。

这里的“原生”指 BusyBox PID 1 启动的 Linux，不依赖 Android 用户空间或 libhybris。GPU 仍使用厂商 KGSL 内核接口；这不代表主线 MSM GPU 驱动或主线 6.18 已在平板工作。`/dev/dri/renderD128` 属于 `msm_drm` 的 SDE 显示设备，不能凭该节点认定它是 3D GPU。

## 已验证的范围

- 私有 Turnip 枚举为 `Turnip Adreno (TM) 650`，vendor `0x5143`、device `0x6050002`、Mesa Turnip driver ID 18。
- [vulkan-smoke.c](../../../userspace/native/gpu/vulkan-smoke.c) 实际执行 16×16 GPU 清屏、图像复制与 host 读回；256 个像素均为预期 RGBA `64,128,191,255`，输出 `GPU_CLEAR_READBACK_PASS`。
- 私有 EGL 显示 `OpenGL ES 3.2`、Zink/Turnip Adreno 650；GLES 离屏读回成功。
- [egl-gbm-smoke.c](../../../userspace/native/gpu/egl-gbm-smoke.c) 在真实 `/dev/dri/card0` 上分配 GBM BO、导出并重新导入 DMA-BUF，GPU 绘制独特 XY 图案；EGL/GLES 与同一 DMA-BUF 的 CPU mmap 按真实 pitch 读回均成功，输出 `GPU_DMA_BUF_RENDER_READBACK_PASS`。root 和 linux UID 1000 均通过。
- Phoc 以 `WLR_RENDERER=gles2` 运行，进程映射了私有 Turnip、EGL、GBM、Gallium 与双 VIG wlroots；grim 得到完整全屏图像，用户确认实体显示与触摸正常。

实际验收材料在 ignored `out/device/native-phosh/gpu-active/`：`screenshot.png`、`session.log`、`app-files.txt`、`gpu-evidence.json`、`physical-acceptance.json`；JSON 记录内核、选中环境、五个库的 SHA 和 UID 1000 各进程私有库映射。root 和 UID 1000 的桥接读回记录分别为 `out/device/native-phosh/first-reboot/egl-gbm-bridge.txt`、`egl-gbm-user.txt`。用户明确反馈：“明显更流畅，显示和触摸正常”。这些是当前运行会话的证据，不代替随后冷启动验收。

UID 1000 的离屏 probe 不持有 DRM master，日志仍有 `drmPrimeFDToHandle Permission denied`；其 PASS 证明 GPU/DMA-BUF 内容读回，不能记为该 probe 已取得 KMS handle。实际显示的验证来自随后通过 seatd 运行的 Phoc 与实体屏幕。

当前 GBM AR24 测试的 stride 为 256，modifier 为 `DRM_FORMAT_MOD_INVALID`，表示隐式布局元数据。Mesa 此路径将 `GBM_BO_USE_LINEAR` 转换为 Vulkan `VK_IMAGE_TILING_LINEAR`，但普通 LINEAR 图像没有填写显式 DRM modifier。独特图案的实际 DMA-BUF 读回确认了该测试缓冲区的线性布局；不能把这一结果扩大到所有格式、UBWC、显式 modifiers 或长期同步稳定性。冷启动、熄屏唤醒和持续负载需要各自的验收记录。

## 源码与构建输入

项目只保存小脚本和一份 Zink 补丁。源码压缩包、Meson wheel、固件、编译目录、共享库和设备日志放在 ignored `out/`，不加入 Git。

| 输入 | 官方来源 | SHA256 |
| --- | --- | --- |
| `mesa_25.2.8.orig.tar.xz` | [Ubuntu Mesa source pool](https://archive.ubuntu.com/ubuntu/pool/main/m/mesa/mesa_25.2.8.orig.tar.xz) | `097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93` |
| `meson-1.9.2-py3-none-any.whl` | [PyPI Meson 1.9.2](https://pypi.org/project/meson/1.9.2/#files) | `1a284dc1912929098a6462401af58dc49ae3f324e94814a38a8f1020cee07cba` |
| [Mesa Zink SM-T870 补丁](../../../userspace/platform/gpu/stage2/mesa-25.2.8-sm-t870-zink-kgsl.patch) | 本项目私有适配 | `53de5362d4679f079dec1d6e6e6eb4439cbde822aaf7de14536f0531c1fa34a1` |

原始 Mesa 源码的 SHA 来自已核验的 Ubuntu `mesa_25.2.8-0ubuntu0.24.04.4.dsc`。该 Ubuntu 包默认只启用 Freedreno MSM KMD；本项目单独编译 KGSL ICD。Mesa 25.2.8 需要 Meson ≥1.4，脚本在发行版 Meson 太旧时从已核验 wheel 创建私有 venv，不替换系统 Meson。

将 `userspace/platform/gpu/` 的五个 `.sh` 和其中 `stage2/` 内补丁保持目录结构复制到一个专用构建目录，例如 native Ubuntu 的 `/root/tabs7-gpu/`；把上述源码包和 wheel 放在两个 Stage 1 脚本旁。不要直接在 tracked `userspace/platform/gpu/` 内下载或编译。以 native arm64 Ubuntu 编译，不需要图形会话：

```sh
apt-get install --no-install-recommends \
  build-essential meson ninja-build pkg-config bison flex patch \
  python3-mako python3-yaml python3-packaging python3-venv \
  libwayland-dev libwayland-egl-backend-dev wayland-protocols \
  zlib1g-dev libzstd-dev libexpat1-dev libdrm-dev libglvnd-dev libvulkan-dev \
  libvulkan1 vulkan-tools

cd /root/tabs7-gpu
export TABS7_TURNIP_PREFIX=/usr/local/lib/tabs7linux/turnip-kgsl
export TABS7_ZINK_PREFIX=/usr/local/lib/tabs7linux/mesa-zink
export TABS7_GPU_BUILD_JOBS=4
sh ./build-turnip-kgsl-native.sh
sh ./stage2/build-zink-egl-gbm-native.sh
```

两个阶段使用独立源码、build 和安装目录。Stage 1 仅构建 KGSL Turnip Vulkan ICD；Stage 2 仅构建 Zink/EGL/GBM/GLVND/GLES，不构建 Vulkan 驱动、LLVM 或其他 Gallium 驱动。两个阶段都设置 `wrap_mode=nofallback`，设备不联网下载子项目。Stage 2 包含 `libwayland-egl-backend-dev` 依赖，缺少它会使首次 configure 失败。

不要把 Stage 1 改成 MSM+KGSL 来添加 GBM：纯 KGSL Turnip 在 Mesa Meson 中关闭 KMS/DRM 支持，而 MSM 混合构建会引入 GPU 与显示设备的 WSI DRM 匹配。Stage 2 的私有 EGL/GBM/Gallium 保持同版本与同目录；Mesa 25 EGL 直接链接 `libgallium-25.2.8.so`，不要求旧 `zink_dri.so` 别名。

重复构建的补丁判断和应用都使用 `patch --forward`；已应用状态仅通过显式 `--reverse --dry-run` 检查。不要删去 `--forward`，否则 GNU patch 的自动反向判断会在重跑时撤销补丁。

## 显示桥接和访问权限

`TABS7_ZINK_KGSL=1` 仅启用私有 Zink 的 DRM screen 桥接：真实 FD 必须报告 `msm_drm`，选中唯一的 Mesa Turnip/Qualcomm A650 KGSL 设备，并确认 DMA-BUF import/export 能力。补丁保留 DPU FD 用于显示 buffer handle，GPU 提交仍通过真实 KGSL FD；它没有伪造 Vulkan DRM 属性。不开该变量保留上游设备选择。

实际所需节点为 `/dev/kgsl-3d0` 和 `/dev/ion`。当前内核没有 `/dev/dma_heap/system`。使用 [70-tabs7-kgsl.rules](../../../userspace/platform/gpu/70-tabs7-kgsl.rules) 为这两个精确节点设置 `render:0660`，linux 用户加入 render 组；规则不扩大为所有 misc 节点。从已复制的项目 `userspace/platform/` 目录以 native Ubuntu root 执行：

```sh
install -m 0644 ./gpu/70-tabs7-kgsl.rules /etc/udev/rules.d/70-tabs7-kgsl.rules
usermod -aG render linux
```

启动器在 chroot 内以 `SYSTEMD_IGNORE_CHROOT=1` 重新加载 udev 规则并触发 drm、input、kgsl 和 misc；新会话的补充组由 `setpriv --init-groups` 获取。先以 root 做受限诊断，再确认 UID 1000 的同一测试通过，正常桌面以 linux 用户运行。

A650 固件来自该设备匹配的原厂包：`a650_sqe.fw`、`a650_gmu.bin`、`a650_zap.mdt` 和实际的 `a650_zap.b00/b01/b02`。固件不进入本项目 Git。[prepare-gpu.py](../../../userspace/platform/prepare-gpu.py) 根据 `/etc/tabs7linux/gpu-firmware.json` 检查精确名称、大小和 SHA，并确认 native 内核看到的目录与 Ubuntu `/usr/lib/firmware` 是同一目录，随后设置内核固件搜索路径 `/linux/lib/firmware`。不要用另一个设备的固件或只按文件名判断。

## Phoc 启动与回退

当前安装目录：

| 组件 | 路径 |
| --- | --- |
| Turnip ICD | `/usr/local/lib/tabs7linux/turnip-kgsl/turnip-kgsl.icd.json` |
| Zink/EGL/GBM/Gallium | `/usr/local/lib/tabs7linux/mesa-zink/lib/` |
| EGL vendor manifest | `/usr/local/lib/tabs7linux/mesa-zink/egl-zink-kgsl.json` |
| 双 VIG wlroots | `/usr/local/lib/tabs7linux/wlroots/lib/libwlroots.so.12` |

当前 wlroots SHA 为 `9c1bee19726702e8fb38bb5b66aeac520cc1794fbd6f305cf3cb604e1afb61fc`，包含双 VIG、layer-shell 兼容和[渲染完成同步](wlroots-render-sync.md)补丁。保留 `WLR_DRM_SM_T870_SPLIT=65,86`、原子提交、DSI 旋转和触摸配置；关闭 GPU 不能同时关闭已验证的双 VIG 显示方案。

SteamOS 会话由 [session.sh](../../userspace/steamos/session.sh) 拉起：它先起 udev、系统 D-Bus 与 seatd（`SEATD_VTBOUND=0`，本内核无 VT），再以 steamos 用户启动使用私有 wlroots 的 Sway 宿主，Steam 会话嵌在其中。Game Mode 由
[gamescope-session](../../userspace/steamos/gamescope-session) 用私有 gamescope `--backend drm` 直接持 KMS。两者的库布局与 Ubuntu 时期一致，因此 ICD/EGL manifest 仍使用绝对路径：

| 会话 | 私有库前缀 |
| --- | --- |
| Sway 宿主 | `mesa-zink/` + `wlroots/` |
| Game Mode（Steam/CEF） | `mesa-zink-gnome-x11/`、`gamescope-sm-t870/` |
| Vulkan | `turnip-kgsl/`、`turnip-steamos-wsi/` |

Xwayland 的私有 KGSL/GBM 环境由 [xwayland-kgsl.sh](../../userspace/platform/xwayland-kgsl.sh) 提供，[xwayland-no-sysvipc.sh](../../userspace/platform/xwayland-no-sysvipc.sh) 是禁用 MIT-SHM 的旧分支。这些验收不能推定所有 GUI 应用均使用 GPU。

内核没有 VT（当前 4.19 变体才补了 SYSVIPC），启动使用 seatd，不依赖 TTY 切换。单独诊断可使用 [run-turnip-kgsl.sh](../../../userspace/platform/gpu/run-turnip-kgsl.sh)、[run-zink-kgsl.sh](../../../userspace/platform/gpu/stage2/run-zink-kgsl.sh) 隔离环境；[distribution-gpu-child.sh](../../../userspace/platform/gpu/stage2/distribution-gpu-child.sh) 可清除 GPU 环境做对照。它们不修改系统 ICD、库链接或启动 marker。
