#!/bin/sh
# Run inside native arm64 Ubuntu. Install only into the separate SteamOS tree.
set -eu
task_archive=${1:-/usr/local/src/tabs7linux/gpu-work/mesa_25.2.8.orig.tar.xz}
task_root=${TABS7_STEAMOS_ROOT:-/steamos}
task_work=${TABS7_STEAMOS_GPU_WORK:-/usr/local/src/tabs7linux/steamos-gpu}
task_jobs=${TABS7_GPU_BUILD_JOBS:-3}
task_meson=/usr/local/src/tabs7linux/gpu-work/build-tools/bin/meson
task_prefix=/usr/local/lib/tabs7linux/turnip-steamos-wsi
task_sha=097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93
task_patch=${TABS7_STEAMOS_WSI_PATCH:-$(dirname "$0")/../../../userspace/steamos/patches/mesa-25.2.8-kgsl-wayland-wsi.patch}
task_source=$task_work/mesa-25.2.8
task_build=$task_work/build-turnip-wsi

fail() { printf 'SteamOS Turnip build: %s\n' "$*" >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) fail 'run on native arm64 Ubuntu' ;; esac
case "$task_root" in /*) ;; *) fail 'SteamOS root must be absolute' ;; esac
[ "$task_root" != / ] || fail 'choose a separate SteamOS root'
[ "$(sed -n 's/^ID=//p' "$task_root/usr/lib/os-release")" = steamos ] || fail 'target is not a SteamOS tree'
case "$task_work" in /usr/local/src/tabs7linux/*) ;; *) fail 'use a dedicated project build directory' ;; esac
case "$task_jobs" in ''|*[!0-9]*|0) fail 'job count must be positive' ;; esac
[ -x "$task_meson" ] || fail 'the existing private Meson tool is missing'
[ -r "$task_archive" ] || fail 'copy the pinned Mesa source archive first'
printf '%s  %s\n' "$task_sha" "$task_archive" | sha256sum --check --status || fail 'Mesa archive hash mismatch'
pkg-config --exists libdrm wayland-client wayland-protocols x11 x11-xcb xcb-dri3 xcb-present xshmfence || fail 'native build dependencies are missing'
mkdir -p "$task_work"
if [ ! -f "$task_source/.tabs7-steamos-source" ]; then
    [ ! -e "$task_source" ] || fail 'source directory exists without a verified marker'
    python3 - "$task_archive" "$task_work" "$task_sha" <<'PY'
from pathlib import Path
import sys,tarfile
archive,destination,digest=sys.argv[1:]
with tarfile.open(archive,'r:xz') as source:
    if any(not entry.name.startswith('mesa-25.2.8/') for entry in source.getmembers()
           if entry.name != 'mesa-25.2.8'):
        raise SystemExit('unexpected Mesa archive root')
    source.extractall(destination,filter='data')
Path(destination,'mesa-25.2.8','.tabs7-steamos-source').write_text(digest+'\n')
PY
fi
[ "$(cat "$task_source/.tabs7-steamos-source")" = "$task_sha" ] || fail 'source marker mismatch'

# Keep DRM image-buffer WSI without advertising unsupported KHR_display.
printf '%s  %s\n' 'a76646d9a28ae52f299986104d7964472232cb1ba6140168e7902fc3cc64b893' "$task_patch" | sha256sum --check --status || fail 'KGSL Wayland patch hash mismatch'
[ -r "$task_patch" ] || fail 'the pinned KGSL Wayland patch is missing'
if patch --batch --forward --dry-run --silent -d "$task_source" -p1 < "$task_patch"; then
    patch --batch --forward --silent -d "$task_source" -p1 < "$task_patch"
elif ! patch --batch --dry-run --reverse --silent -d "$task_source" -p1 < "$task_patch"; then
    fail 'KGSL Wayland patch does not match the pinned source'
fi
set -- "$task_build" "$task_source" --prefix="$task_prefix" --libdir=lib \
    --buildtype=release -Db_ndebug=true -Db_lto=false -Dwrap_mode=nofallback \
    -Dplatforms=x11,wayland -Dgallium-drivers= -Dvulkan-drivers=freedreno \
    -Dfreedreno-kmds=kgsl -Dvulkan-layers= \
    -Degl=disabled -Dgbm=disabled -Dglx=disabled \
    -Dgles1=disabled -Dgles2=disabled -Dopengl=false \
    -Dllvm=disabled -Dshared-llvm=disabled -Dxmlconfig=disabled \
    -Dvalgrind=disabled -Dbuild-tests=false -Dtools=
if [ -f "$task_build/meson-private/coredata.dat" ]; then
    "$task_meson" setup --reconfigure "$@"
else
    "$task_meson" setup "$@"
fi
python3 - "$task_build/compile_commands.json" <<'PY'
import json,sys
commands=json.load(open(sys.argv[1]))
wsi=[entry for entry in commands if entry['file'].endswith('/wsi_common.c')]
assert wsi and all('-DHAVE_LIBDRM' in entry['command'] for entry in wsi), 'DRM WSI must be compiled'
assert all('-DVK_USE_PLATFORM_DISPLAY_KHR' not in entry['command'] for entry in wsi), 'KGSL cannot expose direct display'
assert all('-DVK_USE_PLATFORM_XLIB_KHR' in entry['command'] for entry in wsi), 'Steam requests Xlib WSI'
assert any(entry['file'].endswith('/wsi_common_drm.c') for entry in commands), 'DRM image implementation missing'
print('DRM WSI compile configuration verified')
PY
ninja -C "$task_build" -j "$task_jobs"
"$task_meson" install -C "$task_build" --no-rebuild --destdir "$task_root"
python3 - "$task_root" "$task_prefix" "$task_sha" <<'PY'
from pathlib import Path
import hashlib,json,sys
root,prefix,digest=sys.argv[1:]
installed=Path(root)/prefix.lstrip('/')
library=installed/'lib/libvulkan_freedreno.so'
data=json.loads((installed/'share/vulkan/icd.d/freedreno_icd.aarch64.json').read_text())
data['ICD']['library_path']=prefix+'/lib/libvulkan_freedreno.so'
(installed/'turnip-steamos-wsi.icd.json').write_text(json.dumps(data,indent=2)+'\n')
(installed/'build-verification.json').write_text(json.dumps({
    'mesa':'25.2.8','source_archive_sha256':digest,'kmds':['kgsl'],'platforms':['x11','wayland'],
    'HAVE_LIBDRM':True,'KHR_display':False,'library_sha256':hashlib.sha256(library.read_bytes()).hexdigest(),
    'hardware_rendering_verified':False},indent=2)+'\n')
print('Private SteamOS ICD installed:',installed/'turnip-steamos-wsi.icd.json')
PY
