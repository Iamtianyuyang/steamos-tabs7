#!/bin/sh
# Build only the matching upstream Turnip KGSL ICD into a private prefix.
# Run on native arm64 Ubuntu; this script does not install packages or touch DRM.
set -eu
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
task_archive=$task_dir/mesa_25.2.8.orig.tar.xz
task_source=$task_dir/mesa-25.2.8
task_build=$task_dir/build-turnip-kgsl
task_prefix=${TABS7_TURNIP_PREFIX:-$task_dir/install-turnip-kgsl}
task_jobs=${TABS7_GPU_BUILD_JOBS:-4}
task_sha=097842f3e49d996868b38688db87b006f7d4541e93ce86d2f341d8b3e7be7c93

fail() { printf 'Turnip build: %s\n' "$*" >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) fail 'run this native build on arm64 Ubuntu' ;; esac
case "$task_prefix" in /*) ;; *) fail 'TABS7_TURNIP_PREFIX must be absolute' ;; esac
case "$task_prefix" in /|/usr|/usr/local|/lib|/lib64|/opt) fail 'choose a dedicated private subdirectory' ;; esac
case "$task_jobs" in ''|*[!0-9]*) fail 'TABS7_GPU_BUILD_JOBS must be a positive integer' ;; esac
[ "$task_jobs" -gt 0 ] || fail 'TABS7_GPU_BUILD_JOBS must be positive'
for task_command in python3 ninja pkg-config cc c++ bison flex sha256sum; do
    command -v "$task_command" >/dev/null || fail "missing build tool: $task_command (see docs/steamos/gpu-stack.md)"
done
python3 -c 'import mako, yaml, packaging' || fail 'install python3-mako, python3-yaml and python3-packaging'
pkg-config --exists wayland-client wayland-protocols || fail 'install libwayland-dev and wayland-protocols'
task_meson_version=0
if command -v meson >/dev/null; then task_meson_version=$(meson --version); fi
if ! python3 - "$task_meson_version" <<'PY'
import sys
from packaging.version import Version
raise SystemExit(0 if Version(sys.argv[1]) >= Version('1.4.0') else 1)
PY
then
    # Noble's packaged Meson can be 1.3.2; Mesa 25.2.8 requires >=1.4.0.
    # Bootstrap a pinned pure-Python wheel into this private build directory.
    task_wheel=$task_dir/meson-1.9.2-py3-none-any.whl
    [ -r "$task_wheel" ] || fail 'copy meson-1.9.2-py3-none-any.whl beside the script'
    printf '%s  %s\n' 1a284dc1912929098a6462401af58dc49ae3f324e94814a38a8f1020cee07cba "$task_wheel" | \
        sha256sum --check --status || fail 'Meson wheel checksum mismatch'
    python3 -m venv --system-site-packages "$task_dir/build-tools" || fail 'install python3-venv'
    "$task_dir/build-tools/bin/python3" -m pip install --no-index --no-deps "$task_wheel"
    PATH="$task_dir/build-tools/bin:$PATH"
    export PATH
fi
[ -r "$task_archive" ] || fail "copy the verified archive beside this script: $task_archive"
printf '%s  %s\n' "$task_sha" "$task_archive" | sha256sum --check --status || fail 'Mesa source checksum mismatch'

if [ ! -f "$task_source/.tabs7-complete-source" ]; then
    # data filter rejects paths escaping this directory and unsafe link targets.
    python3 - "$task_archive" "$task_dir" <<'PY'
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

set -- "$task_build" "$task_source" \
    --prefix="$task_prefix" --libdir=lib \
    --buildtype=release -Db_ndebug=true -Db_lto=false -Dwrap_mode=nofallback \
    -Dplatforms=wayland -Dgallium-drivers= -Dvulkan-drivers=freedreno \
    -Dfreedreno-kmds=kgsl -Dvulkan-layers= \
    -Degl=disabled -Dgbm=disabled -Dglx=disabled \
    -Dgles1=disabled -Dgles2=disabled -Dopengl=false \
    -Dllvm=disabled -Dshared-llvm=disabled \
    -Dxmlconfig=disabled -Dvalgrind=disabled -Dbuild-tests=false -Dtools=
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
library = prefix / 'lib/libvulkan_freedreno.so'
manifest = prefix / 'share/vulkan/icd.d/freedreno_icd.aarch64.json'
if not library.is_file() or not manifest.is_file():
    raise SystemExit('private Turnip library or generated arm64 ICD manifest is missing')
data = json.loads(manifest.read_text())
data['ICD']['library_path'] = str(library)
isolated = prefix / 'turnip-kgsl.icd.json'
isolated.write_text(json.dumps(data, indent=2) + '\n')
print('Private ICD:', isolated)
print('API version:', data['ICD']['api_version'])
PY
printf 'Build complete. No system Mesa libraries or ICD directories were changed.\n'
