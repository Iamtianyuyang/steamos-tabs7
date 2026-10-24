#!/bin/sh
# The native kernel has no SYSVIPC; DRI3 uses real DMA-BUF FDs, not MIT-SHM.
set -eu
if [ -f /etc/tabs7linux/xwayland-kgsl-enabled ] && [ "${WLR_RENDERER:-}" = gles2 ]; then
    BASE=$(dirname "$(readlink -f "$0")")
    exec "$BASE/xwayland-kgsl.sh" "$@"
fi
unset LD_LIBRARY_PATH
unset LIBGL_DRIVERS_PATH __EGL_VENDOR_LIBRARY_FILENAMES
unset MESA_LOADER_DRIVER_OVERRIDE GALLIUM_DRIVER TABS7_ZINK_KGSL
unset VK_DRIVER_FILES VK_ICD_FILENAMES TU_DEBUG TABS7_WLROOTS_RENDER_FINISH
exec /usr/bin/Xwayland "$@" -shm -extension MIT-SHM -nolisten tcp
