#!/bin/sh
# Build only private Zink + EGL/GBM. Keep the Stage 1 KGSL-only ICD unchanged.
set -eu
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
task_stage=$task_dir/stage2
task_archive=$task_dir/mesa_25.2.8.orig.tar.xz
task_source=$task_stage/source/mesa-25.2.8
task_build=$task_stage/build-zink-egl-gbm
task_prefix=${TABS7_ZINK_PREFIX:-$task_dir/install-zink-egl-gbm}
task_jobs=${TABS7_GPU_BUILD_JOBS:-4}
task_patch=$task_stage/mesa-25.2.8-sm-t870-zink-kgsl.patch
fail() { printf 'Zink build: %s\n' "$*" >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) fail 'run this native build on arm64 Ubuntu' ;; esac
case "$task_prefix" in /*) ;; *) fail 'TABS7_ZINK_PREFIX must be absolute' ;; esac
case "$task_prefix" in /|/usr|/usr/local|/lib|/lib64|/opt) fail 'choose a dedicated private subdirectory' ;; esac
case "$task_jobs" in ''|*[!0-9]*) fail 'TABS7_GPU_BUILD_JOBS must be a positive integer' ;; esac
[ "$task_jobs" -gt 0 ] || fail 'TABS7_GPU_BUILD_JOBS must be positive'
for task_command in python3 ninja pkg-config cc c++ bison flex patch sha256sum; do
    command -v "$task_command" >/dev/null || fail "missing build tool: $task_command"
done
python3 -c 'import mako, yaml, packaging' || fail 'install python3-mako/yaml/packaging'
pkg-config --exists wayland-client wayland-protocols libdrm libglvnd || \
    fail 'install libwayland-dev, wayland-protocols, libdrm-dev, libglvnd-dev'
task_meson_version=0
if command -v meson >/dev/null; then task_meson_version=$(meson --version); fi
if ! python3 - "$task_meson_version" <<'PY'
import sys
from packaging.version import Version
raise SystemExit(0 if Version(sys.argv[1]) >= Version('1.4.0') else 1)
PY
then
    task_wheel=$task_dir/meson-1.9.2-py3-none-any.whl
    [ -r "$task_wheel" ] || fail 'copy the Stage 1 pinned Meson wheel beside its archive'
    printf '%s  %s\n' 1a284dc1912929098a6462401af58dc49ae3f324e94814a38a8f1020cee07cba "$task_wheel" | \
        sha256sum --check --status || fail 'Meson wheel checksum mismatch'
    python3 -m venv --system-site-packages "$task_dir/build-tools" || fail 'install python3-venv'
    "$task_dir/build-tools/bin/python3" -m pip install --no-index --no-deps "$task_wheel"
    PATH="$task_dir/build-tools/bin:$PATH"
    export PATH
fi
printf '%s  %s\n' 097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93 "$task_archive" | \
    sha256sum --check --status || fail 'Mesa source checksum mismatch'
printf '%s  %s\n' 53de5362d4679f079dec1d6e6e6eb4439cbde822aaf7de14536f0531c1fa34a1 "$task_patch" | \
    sha256sum --check --status || fail 'Stage 2 patch checksum mismatch'
mkdir -p "$task_stage/source"
if [ ! -f "$task_source/.tabs7-complete-source" ]; then
    python3 - "$task_archive" "$task_stage/source" <<'PY'
import sys, tarfile
from pathlib import Path
archive, destination = sys.argv[1:]
with tarfile.open(archive, 'r:xz') as source:
    for entry in source:
        parts = Path(entry.name).parts
        if not parts or parts[0] != 'mesa-25.2.8':
            raise SystemExit('unexpected Mesa archive root')
    source.extractall(destination, filter='data')
Path(destination, 'mesa-25.2.8', '.tabs7-complete-source').write_text('25.2.8\n')
PY
fi
if patch --dry-run --batch --forward -p1 -d "$task_source" < "$task_patch" >/dev/null 2>&1; then
    patch --batch --forward -p1 -d "$task_source" < "$task_patch"
elif patch --dry-run --batch --forward --reverse -p1 -d "$task_source" < "$task_patch" >/dev/null 2>&1; then
    printf 'Stage 2 patch is already applied.\n'
else
    fail 'private source does not match the reviewed patch'
fi
set -- "$task_build" "$task_source" --prefix="$task_prefix" --libdir=lib \
    --buildtype=release -Db_ndebug=true -Db_lto=false -Dwrap_mode=nofallback \
    -Dplatforms=wayland -Dgallium-drivers=zink -Dvulkan-drivers= -Dvulkan-layers= \
    -Degl=enabled -Dgbm=enabled -Dglx=disabled -Dglvnd=enabled \
    -Dgles1=disabled -Dgles2=enabled -Dopengl=true \
    -Dllvm=disabled -Dshared-llvm=disabled -Dxmlconfig=disabled \
    -Dvalgrind=disabled -Dbuild-tests=false -Dtools=
if [ -f "$task_build/meson-private/coredata.dat" ]; then
    meson setup --reconfigure "$@"
else
    meson setup "$@"
fi
ninja -C "$task_build" -j "$task_jobs"
meson install -C "$task_build" --no-rebuild
python3 - "$task_prefix" <<'PY'
import json, sys
from pathlib import Path
prefix = Path(sys.argv[1]).resolve()
egl = prefix / 'lib/libEGL_mesa.so.0'
gbm = prefix / 'lib/libgbm.so.1'
driver = prefix / 'lib/libgallium-25.2.8.so'
if not all(p.is_file() for p in (egl, gbm, driver)):
    raise SystemExit('private EGL vendor library, GBM, or Zink Gallium library is missing')
manifest = prefix / 'egl-zink-kgsl.json'
manifest.write_text(json.dumps({'file_format_version': '1.0.0',
                               'ICD': {'library_path': str(egl)}}, indent=2) + '\n')
print('Private EGL vendor manifest:', manifest)
PY
printf 'Private Zink/EGL/GBM built; system Mesa and Stage 1 Turnip were not replaced.\n'
