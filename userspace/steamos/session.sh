#!/bin/bash
# SteamOS session supervisor for the SM-T870.
#
# Runs as root inside the SteamOS rootfs (chroot from the native initramfs).
# Brings up the services SteamOS normally gets from systemd/logind, then starts
# the patched-wlroots Sway host as the SteamOS user; Sway owns the two-VIG
# scanout and the Steam session nests inside it.
set -eu

PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

RUNTIME=/run/user/1000
USER_NAME=steamos
USER_ID=1000
SELF_DIR=$(cd "$(dirname "$0")" && pwd)
LOG=/run/tabs7-steamos.log

die() { printf 'SteamOS session failed: %s\nLog: %s\n' "$*" "$LOG" >&2; exit 1; }

exec >>"$LOG" 2>&1
printf '\n=== SteamOS session %s ===\n' "$(date -u)"

[ "$(id -u)" -eq 0 ] || die 'UID 0 is required'
[ -x /usr/bin/gamescope ] || die '/usr/bin/gamescope is missing'
[ -x /usr/bin/steam ] || die '/usr/bin/steam is missing'
[ -x /usr/local/lib/tabs7linux/host/bin/sway ] || die 'the display-host sway is missing'
[ -c /dev/dri/card0 ] || die '/dev/dri/card0 is unavailable (bind /dev into the rootfs)'

mkdir -p "$RUNTIME" /run/dbus /run/seatd
chown "$USER_ID:$USER_ID" "$RUNTIME"
chmod 0700 "$RUNTIME"

# udev: SteamOS ships systemd-udevd but systemd is not PID 1 here.
export SYSTEMD_IGNORE_CHROOT=1
if ! udevadm control --timeout=5 --reload >/dev/null 2>&1; then
    udevd=/usr/lib/systemd/systemd-udevd
    [ -x "$udevd" ] || die 'systemd-udevd is missing'
    "$udevd" --daemon >>/run/tabs7-udev.log 2>&1
fi
udevadm trigger --action=add --subsystem-match=input --subsystem-match=drm \
    --subsystem-match=kgsl --subsystem-match=misc >>/run/tabs7-udev.log 2>&1 || true
udevadm settle --timeout=15 >>/run/tabs7-udev.log 2>&1 || true

# System bus (no logind, no systemd units).
dbus-uuidgen --ensure=/etc/machine-id
mkdir -p /var/lib/dbus
[ -e /var/lib/dbus/machine-id ] || ln -sf /etc/machine-id /var/lib/dbus/machine-id
if ! dbus-send --system --type=method_call --print-reply --dest=org.freedesktop.DBus \
        / org.freedesktop.DBus.GetId >/dev/null 2>&1; then
    dbus-daemon --system --fork --print-pid >/run/dbus/pid
fi

# seatd: grants DRM master without a VT (this kernel has no VT support).
if [ ! -S /run/seatd.sock ]; then
    SEATD_VTBOUND=0 /usr/local/lib/tabs7linux/host/bin/seatd \
        -u "$USER_NAME" -g video -l info >/run/seatd.log 2>&1 &
    count=0
    while [ ! -S /run/seatd.sock ]; do
        count=$((count + 1))
        [ "$count" -lt 50 ] || die 'seatd did not create /run/seatd.sock'
        sleep 0.1
    done
fi

# Private GPU stack: Turnip over KGSL + Zink for GL, same layout the Ubuntu
# host uses, so the ICD/EGL manifests keep their absolute paths.
export XDG_RUNTIME_DIR="$RUNTIME"
export HOME=/home/steamos
export XDG_CURRENT_DESKTOP=gamescope
export XDG_SESSION_TYPE=wayland
export LD_LIBRARY_PATH=/usr/local/lib/tabs7linux/mesa-zink/lib:/usr/local/lib/tabs7linux/wlroots/lib
export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/local/lib/tabs7linux/mesa-zink/egl-zink-kgsl.json
export LIBGL_DRIVERS_PATH=/usr/local/lib/tabs7linux/mesa-zink/lib/dri
export VK_DRIVER_FILES=/usr/local/lib/tabs7linux/turnip-kgsl/turnip-kgsl.icd.json
export VK_ICD_FILENAMES=/usr/local/lib/tabs7linux/turnip-kgsl/turnip-kgsl.icd.json
export MESA_LOADER_DRIVER_OVERRIDE=zink
export GALLIUM_DRIVER=zink
export TABS7_ZINK_KGSL=1
export TU_DEBUG=noubwc
export TABS7_WLROOTS_RENDER_FINISH=1
export WLR_BACKENDS=drm,libinput
export WLR_RENDERER=gles2
export WLR_DRM_DEVICES=/dev/dri/card0
export WLR_DRM_NO_MODIFIERS=1
export WLR_NO_HARDWARE_CURSORS=1
export WLR_DRM_FORCE_LIBLIFTOFF=0
export WLR_DRM_NO_ATOMIC=0
export WLR_LIBINPUT_NO_DEVICES=1

# The two plane IDs are physical facts of this kernel's DPU layout.
split=$(cat /etc/tabs7linux/wlroots-sm-t870-split 2>/dev/null || true)
case "$split" in
    ''|*[!0-9,]*) die 'the split profile /etc/tabs7linux/wlroots-sm-t870-split is missing' ;;
esac

printf 'starting the sway host (split %s)\n' "$split"
export WLR_DRM_SM_T870_SPLIT="$split"
exec setpriv --reuid="$USER_NAME" --regid="$USER_NAME" --init-groups \
    /usr/local/lib/tabs7linux/host/bin/sway \
        -c /usr/local/lib/tabs7linux/steamos/sway.conf \
        -d >>/run/tabs7-sway.log 2>&1
