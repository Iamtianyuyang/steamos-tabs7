#!/bin/sh
# Per-command EGL-X11 experiment; does not change the desktop renderer/profile.
set -eu
task_zink=${TABS7_GNOME_ZINK_PREFIX:-/usr/local/lib/tabs7linux/mesa-zink-gnome-x11}
task_turnip=${TABS7_GNOME_TURNIP_PREFIX:-/usr/local/lib/tabs7linux/turnip-kgsl}
[ "$#" -gt 0 ] || { printf 'Usage: %s COMMAND [ARG ...]\n' "$0" >&2; exit 2; }
[ -r "$task_zink/egl-zink-kgsl.json" ] && [ -r "$task_turnip/turnip-kgsl.icd.json" ] || \
    { printf 'Independent X11 EGL prefix and Turnip ICD are required.\n' >&2; exit 1; }
export LD_LIBRARY_PATH="$task_zink/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LIBGL_DRIVERS_PATH="$task_zink/lib/dri"
export __EGL_VENDOR_LIBRARY_FILENAMES="$task_zink/egl-zink-kgsl.json"
export VK_DRIVER_FILES="$task_turnip/turnip-kgsl.icd.json" VK_ICD_FILENAMES="$task_turnip/turnip-kgsl.icd.json"
export MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink TABS7_ZINK_KGSL=1
export EGL_PLATFORM=x11 LIBGL_KOPPER_DISABLE=1
export TABS7_ZINK_X11_PRESENT=1
export TU_DEBUG="${TU_DEBUG:+$TU_DEBUG,}noubwc"
unset LIBGL_ALWAYS_SOFTWARE D3D_ALWAYS_SOFTWARE LIBGL_KOPPER_DRI2
exec "$@"
