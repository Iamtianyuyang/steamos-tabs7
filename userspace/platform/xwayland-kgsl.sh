#!/bin/sh
# Private GBM renderer for Xwayland; these exports never enter its clients.
set -eu
task_zink=/usr/local/lib/tabs7linux/mesa-zink
task_turnip=/usr/local/lib/tabs7linux/turnip-kgsl
for task_file in "$task_zink/lib/libgbm.so.1" \
                 "$task_zink/lib/libEGL_mesa.so.0" \
                 "$task_zink/lib/libgallium-25.2.8.so" \
                 "$task_zink/egl-zink-kgsl.json" \
                 "$task_turnip/turnip-kgsl.icd.json"; do
    [ -r "$task_file" ] || { printf 'Xwayland KGSL: missing %s\n' "$task_file" >&2; exit 1; }
done
for task_arg do
    case "$task_arg" in
        -shm|-eglstream) printf 'Xwayland KGSL refuses %s\n' "$task_arg" >&2; exit 2 ;;
    esac
done
unset LD_PRELOAD LD_AUDIT LD_DEBUG LD_DEBUG_OUTPUT
unset G_RESOURCE_OVERLAYS CLUTTER_SHOW_FPS CLUTTER_DEBUG CLUTTER_PAINT COGL_DEBUG
unset COGL_DRIVER MUTTER_DEBUG_DUMMY_MODE_SPECS MUTTER_DEBUG_NUM_DUMMY_MONITORS
unset MUTTER_DEBUG_DUMMY_MONITOR_SCALES MUTTER_DEBUG_FORCE_KMS_MODE
unset TABS7_XI_STAGE_OWNER_FIX TABS7_WLROOTS_RENDER_FINISH
unset TABS7_PHOC_FINISH_TIMING TABS7_PHOC_FINISH_TIMING_EXE
unset TABS7_XCB_TRACE TABS7_XCB_TRACE_FILE TABS7_XCB_TIMING TABS7_XCB_TIMING_FILE
unset TABS7_ZINK_X11_PRESENT TABS7_ZINK_X11_PRESENT_TRACE
unset WAYLAND_DEBUG G_MESSAGES_DEBUG EGL_LOG_LEVEL LIBGL_DEBUG MESA_DEBUG
unset MESA_LOG_LEVEL MESA_LOG_FILE VK_LOADER_DEBUG VK_INSTANCE_LAYERS
unset VK_LAYER_PATH VK_ADD_LAYER_PATH ZINK_DEBUG GALLIUM_HUD GALLIUM_DDEBUG
unset GALLIUM_TRACE GALLIUM_TRACE_TC GALLIUM_PRINT_OPTIONS
unset DRI_PRIME DRI_PRIME_DEBUG LIBGL_ALWAYS_SOFTWARE D3D_ALWAYS_SOFTWARE
unset LIBGL_KOPPER_DRI2 LIBGL_KOPPER_DRI3 SWRAST_NO_PRESENT
unset EGL_PLATFORM GBM_BACKEND GBM_BACKENDS_PATH XWAYLAND_NO_GLAMOR
export LD_LIBRARY_PATH="$task_zink/lib"
export LIBGL_DRIVERS_PATH="$task_zink/lib/dri"
export __EGL_VENDOR_LIBRARY_FILENAMES="$task_zink/egl-zink-kgsl.json"
export VK_DRIVER_FILES="$task_turnip/turnip-kgsl.icd.json"
export VK_ICD_FILENAMES="$VK_DRIVER_FILES"
export MESA_LOADER_DRIVER_OVERRIDE=zink GALLIUM_DRIVER=zink TABS7_ZINK_KGSL=1
export LIBGL_KOPPER_DISABLE=1 TU_DEBUG=noubwc,sysmem
exec /usr/bin/Xwayland "$@" -extension MIT-SHM -nolisten tcp
