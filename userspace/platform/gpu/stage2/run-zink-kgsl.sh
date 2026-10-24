#!/bin/sh
# Apply the experimental GPU stack only to this command and its children.
set -eu
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_turnip=${TABS7_TURNIP_PREFIX:-$task_dir/install-turnip-kgsl}
task_zink=${TABS7_ZINK_PREFIX:-$task_dir/install-zink-egl-gbm}
[ "$#" -gt 0 ] || { printf 'Usage: %s COMMAND [ARG ...]\n' "$0" >&2; exit 2; }
[ -r "$task_turnip/turnip-kgsl.icd.json" ] && [ -r "$task_zink/egl-zink-kgsl.json" ] || \
    { printf 'Private Stage 1 and Stage 2 manifests are required.\n' >&2; exit 1; }
export VK_DRIVER_FILES="$task_turnip/turnip-kgsl.icd.json"
export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
export __EGL_VENDOR_LIBRARY_FILENAMES="$task_zink/egl-zink-kgsl.json"
export LIBGL_DRIVERS_PATH="$task_zink/lib/dri"
export LD_LIBRARY_PATH="$task_zink/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink TABS7_ZINK_KGSL=1
unset LIBGL_ALWAYS_SOFTWARE D3D_ALWAYS_SOFTWARE
# This diagnostic keeps the known display path linear; it does not select gles2.
export WLR_DRM_NO_MODIFIERS=1
export TU_DEBUG="${TU_DEBUG:+$TU_DEBUG,}noubwc"
exec "$@"
