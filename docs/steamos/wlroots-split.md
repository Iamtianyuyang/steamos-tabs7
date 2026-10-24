# SM-T870 private wlroots build

This patch targets Ubuntu Noble's `wlroots 0.17.1-2.1build1`, used by
`sway 1.9-1build2`. Build it inside the arm64 Ubuntu rootfs. It keeps the
public API and `libwlroots.so.12` SONAME; install the result in a private
directory rather than replacing Ubuntu's package.

The native arm64 Meson/Ninja build completed all 321 steps and linked
successfully. The resulting library is AArch64, has SONAME `libwlroots.so.12`,
and exports exactly the same 754 symbols as Ubuntu's original library.
The early XFCE trial library SHA256 was
`c9cba78f164af6e9fb1ffdefbff6f077e6ceaf982eada01feadae3bf181e2a4f`.
The earlier Phoc-compatible library SHA256 was
`d2855e5db6319ee49a8f42b59f91fd117830b46443a4cc9c01e3f26c4b1d3ee6`.
It additionally restores Ubuntu's
`Revert-layer-shell-error-on-0-dimension-without-anchors.patch`, needed by
Phosh 0.38's initial zero-height home layer surface. The early checksum is
historical and must not be used to identify the current desktop library.
The current library SHA256 is
`9c1bee19726702e8fb38bb5b66aeac520cc1794fbd6f305cf3cb604e1afb61fc`;
it also includes the separate [render completion patch](wlroots-render-sync.md),
with the same AArch64 SONAME and 754 exports.
The split patch SHA256 is
`2834ada852e03bbedf38c0eb51e2ca19bc2aac78b4fe4bc2d11f64375f72e606`.
With the private library selected, the user confirmed the complete physical
XFCE desktop and accurate touchscreen clicks and dragging. The library and
profile are persisted. The user also confirmed Onboard on-screen keyboard
letter entry into `xfce4-terminal`. After reboot, Sway/Xwayland/XFCE started
automatically and the user confirmed the full display and aligned touch again.
The private library mapping/checksum, `65,86` profile and Onboard GTK setting
survived reboot with the original 4.19 kernel and BusyBox PID 1.
Host acceptance records and XWD desktop captures use `out/device/native-gui/`
(`acceptance-before-reboot.json`, `xfce-before-reboot.xwd`,
`acceptance-after-reboot.json` and `xfce-after-reboot.xwd`); all four are saved.
Final acceptance uses `acceptance-final.json` and `xfce-final.xwd` in the same
directory, after confirming the touch-friendly screen-keyboard shortcut.

Install the dependencies declared by the exact Ubuntu source package, plus
the native compiler and Ninja. This retains Ubuntu's existing renderer,
backend and Xwayland features and exported symbols:

```sh
apt-get install --no-install-recommends \
  build-essential meson ninja-build pkg-config hwdata glslang-tools \
  libcap-dev libcairo2-dev libdisplay-info-dev libdrm-dev libegl1-mesa-dev \
  libegl-dev libgbm-dev libgles2-mesa-dev libinput-dev libliftoff-dev \
  libpixman-1-dev libpng-dev libseat-dev libsystemd-dev libudev-dev \
  libvulkan-dev libwayland-dev libx11-xcb-dev libxcb1-dev \
  libxcb-composite0-dev libxcb-dri3-dev libxcb-ewmh-dev libxcb-icccm4-dev \
  libxcb-image0-dev libxcb-present-dev libxcb-render0-dev \
  libxcb-render-util0-dev libxcb-res0-dev libxcb-shm0-dev libxcb-xfixes0-dev \
  libxcb-xinput-dev libxkbcommon-dev wayland-protocols xwayland
```

During Recovery, the host APT mirror proxy can be reached through the
parent-controlled ADB reverse connection. Keep Ubuntu signature verification
enabled. No systemd service or graphical session is required for compilation.

Download the exact official source files from
`https://archive.ubuntu.com/ubuntu/pool/universe/w/wlroots/`:

| File | SHA256 |
| --- | --- |
| `wlroots_0.17.1.orig.tar.gz` | `b4b1b08daf0728cd2648fb57660493e5b3eafe4fcc081c138de864895181cdfe` |
| `wlroots_0.17.1-2.1build1.debian.tar.xz` | `46bebf5b92e31cb1155bd7e8ec6f70fcabe6ba0d4bab88023a70acd7f0dfea1e` |

Extract the upstream archive, apply the Ubuntu patch listed in the Debian
archive's `debian/patches/series`, then apply `wlroots-sm-t870-split.patch`.
For the GPU desktop, also apply `wlroots-sm-t870-render-sync.patch` as described
in the completion patch documentation.
Ubuntu's existing patch only changes layer-shell validation.

```sh
patch -p1 --forward --dry-run < /path/to/wlroots-sm-t870-split.patch
patch -p1 --forward < /path/to/wlroots-sm-t870-split.patch
meson setup build --buildtype=release --prefix=/usr/local/lib/tabs7linux/wlroots \
  --libdir=lib -Dbackends=drm,libinput,x11 -Dexamples=false \
  -Dxwayland=enabled -Dsession=enabled -Dwrap_mode=nofallback
ninja -C build -j4
```

Pixman is always built into wlroots 0.17.1. `renderers=pixman` is not a valid
Meson option in this release. Keep the default renderer features to retain
the installed Sway binary's existing ABI.

Install only the resulting library and its SONAME symlink into the private
directory after checking the build result. Check the ELF machine is AArch64,
SONAME is `libwlroots.so.12`, and every dynamic symbol required by the installed
Sway binary remains exported. Select the private directory only for Sway:

```sh
LD_LIBRARY_PATH=/usr/local/lib/tabs7linux/wlroots/lib \
WLR_DRM_SM_T870_SPLIT=65,86 \
WLR_DRM_FORCE_LIBLIFTOFF=0 WLR_DRM_NO_ATOMIC=0 \
WLR_RENDERER=pixman WLR_DRM_DEVICES=/dev/dri/card0 \
WLR_DRM_NO_MODIFIERS=1 WLR_NO_HARDWARE_CURSORS=1 \
sway --config /usr/local/lib/tabs7linux/gui/sway.conf
```

The two decimal plane IDs must come from the current boot's DRM metadata.
`65,86` is the pair physically verified in both the Recovery control and the
native XFCE environment;
another kernel command line can change the plane inventory. An unset variable
or `WLR_DRM_SM_T870_SPLIT=0` retains the standard backend. An invalid enabled
profile aborts backend initialization and logs the failed condition.

The project launcher enables this profile when the plain text file
`/etc/tabs7linux/wlroots-sm-t870-split` contains the selected pair, for example
`65,86` followed by a newline. Create it only after validating the private
library's ELF/exports and completing the hardware trial. An invalid marker or a
private library unreadable by `linux` stops startup before creating services.
Removing or renaming the marker restores the standard library launch path;
`stop` and `status` do not depend on it. This is a diagnostic rollback: the
standard single full-width scanout reproduces the narrow-strip display fault,
so disabling the profile is not recommended for normal desktop use.
The private library search path and split setting apply only
to Sway, so Xwayland, XFCE and helper processes keep their standard libraries.

wlroots 0.17.1 selects its DRM interface with `WLR_DRM_FORCE_LIBLIFTOFF` and
`WLR_DRM_NO_ATOMIC`. The launcher sets both to `0` for the enabled split profile;
this release has no `WLR_DRM_NO_LIBLIFTOFF` option. The patch itself also rejects
an enabled profile if the chosen interface is not atomic.

The profile accepts only atomic `msm_drm`, one GPU, connected `DSI-1`, and two
real Qualcomm VIG planes with a common DSI CRTC, compatible formats and writable
`zpos=0`. It reserves the pair and affected logical CRTC owners for the backend
lifetime; other connectors cannot receive them, and DSI-1 leasing is refused.
Use only the panel's 1600×2560 physical modes. Sway applies the 90-degree desktop
transform before scanout, so the physical split remains two 800×2560 halves.

Both halves share the original framebuffer in one atomic request, including
TEST_ONLY and output disable. The original queued/current buffer ownership and
single CRTC page-flip event remain intact. The logical primary is disabled when
it differs from both VIGs. Retained buffers are validated again on resume; no
additional framebuffer handle, explicit fence or separate commit is introduced.

Hardware evidence precedes the patch: with the same mode, framebuffer, format
and stride, two VIGs drawing 800-pixel halves produced a full physical test grid;
one VIG drawing all 1600 pixels reproduced the upper strip. The patched native
XFCE session now displays the complete landscape desktop. Mapping touchscreen
`0:0:sec_touchscreen` to `DSI-1` also passed user click and drag verification.
Onboard with GTK input passed user letter-entry verification in `xfce4-terminal`.
The persistent launcher selects the private library through the installed
`65,86` profile. Reboot verification passed, including automatic desktop startup
and user-confirmed full physical display and aligned touch. The existing
kernel, DTB and recovery DTBO remain in use.

The separate XFCE helper has applied and persisted one-time desktop single-click
launching, preserving future user changes even with `--force`. The user confirmed
that a single finger tap opens the screen-keyboard shortcut and then types into
the terminal. S Pen, sound, Wi-Fi and GPU acceleration remain untested.
