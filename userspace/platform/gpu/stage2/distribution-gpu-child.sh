#!/bin/sh
# Use as phoc -E's wrapper to leave GNOME/Phosh/OSK on distribution libraries.
# It can also wrap the existing Xwayland-no-SYSVIPC script.
set -eu
[ "$#" -gt 0 ] || { printf 'Usage: %s COMMAND [ARG ...]\n' "$0" >&2; exit 2; }
unset LD_LIBRARY_PATH LIBGL_DRIVERS_PATH __EGL_VENDOR_LIBRARY_FILENAMES
unset MESA_LOADER_DRIVER_OVERRIDE GALLIUM_DRIVER TABS7_ZINK_KGSL
unset VK_DRIVER_FILES VK_ICD_FILENAMES TU_DEBUG
exec "$@"
