#!/bin/sh
# Run as root inside the SteamOS rootfs on the device (native arm64, SteamOS
# toolchain and libraries). Installs only into a separate private prefix; the
# distro /usr/bin/gamescope stays untouched.
set -eu
task_archive=${1:?usage: $0 gamescope-source.tar.gz}
task_work=${TABS7_GAMESCOPE_WORK:-/usr/local/src/tabs7linux/gamescope}
task_jobs=${TABS7_GPU_BUILD_JOBS:-3}
task_prefix=/usr/local/lib/tabs7linux/gamescope-sm-t870
task_version=3.16.30
task_patch=${TABS7_GAMESCOPE_PATCH:-$(dirname "$0")/../../../userspace/steamos/patches/gamescope-3.16.30-sm-t870-split.patch}
task_patch_sha=8b20a6091bbe1999f826baeb8ed86653a7e4b2840ffdd85b9bd6aa07097faf9f
task_source=$task_work/gamescope-$task_version
task_build=$task_work/build

fail() { printf 'SteamOS gamescope build: %s\n' "$*" >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) fail 'run on native arm64' ;; esac
[ "$(sed -n 's/^ID=//p' /usr/lib/os-release)" = steamos ] || fail 'run inside the SteamOS rootfs'
[ "$(id -u)" -eq 0 ] || fail 'run as root'
case "$task_work" in /usr/local/src/tabs7linux/*) ;; *) fail 'use a dedicated project build directory' ;; esac
case "$task_jobs" in ''|*[!0-9]*|0) fail 'job count must be positive' ;; esac
for tool in gcc g++ meson ninja pkg-config; do
    command -v "$tool" >/dev/null || fail "$tool is missing from the SteamOS rootfs"
done
command -v glslang >/dev/null || command -v glslangValidator >/dev/null ||
    fail 'glslang is missing from the SteamOS rootfs'
[ -r "$task_archive" ] || fail 'copy the prepared gamescope source archive first'
[ -r "$task_patch" ] || fail 'the pinned SM-T870 split patch is missing'
printf '%s  %s\n' "$task_patch_sha" "$task_patch" | sha256sum --check --status || fail 'split patch hash mismatch'

# The archive carries submodules and pre-fetched wraps (no network at build time).
mkdir -p "$task_work"
if [ ! -f "$task_source/.tabs7-gamescope-source" ]; then
    [ ! -e "$task_source" ] || fail 'source directory exists without a verified marker'
    # -m: archive mtimes come from the build host clock, not this device.
    tar -m -xzf "$task_archive" -C "$task_work"
    [ -f "$task_source/src/Backends/DRMBackend.cpp" ] || fail 'unexpected archive layout'
    sha256sum "$task_archive" | cut -d' ' -f1 > "$task_source/.tabs7-gamescope-source"
fi
# A copy of the applied revision lets a new patch revision replace it in place,
# so the existing build directory only recompiles what changed.
applied=$task_source/.tabs7-applied.patch
if [ -f "$applied" ] && ! cmp -s "$applied" "$task_patch"; then
    patch --batch --reverse --silent -d "$task_source" -p1 < "$applied" ||
        fail 'previously applied split patch no longer reverses cleanly'
    rm -f "$applied"
fi
if patch --batch --forward --dry-run --silent -d "$task_source" -p1 < "$task_patch"; then
    patch --batch --forward --silent -d "$task_source" -p1 < "$task_patch"
elif ! patch --batch --dry-run --reverse --silent -d "$task_source" -p1 < "$task_patch"; then
    fail 'split patch does not match the pinned source'
fi
cp "$task_patch" "$applied"

set -- "$task_build" "$task_source" --prefix="$task_prefix" --libdir=lib \
    --buildtype=release -Db_ndebug=false -Dlibliftoff:werror=false -Dwrap_mode=nodownload \
    --force-fallback-for=wlroots,libliftoff,vkroots,libdisplay-info,glm,stb \
    -Denable_openvr_support=false -Denable_tests=false -Dbenchmark=disabled \
    -Ddrm_backend=enabled -Dsdl2_backend=enabled -Dpipewire=enabled \
    -Davif_screenshots=enabled -Dinput_emulation=enabled -Drt_cap=enabled
if [ -f "$task_build/meson-private/coredata.dat" ]; then
    meson setup --reconfigure "$@"
else
    meson setup "$@"
fi
ninja -C "$task_build" -j "$task_jobs"
meson install -C "$task_build" --no-rebuild --skip-subprojects
binary=$task_prefix/bin/gamescope
[ -x "$binary" ] || fail 'gamescope binary was not installed'
"$binary" --version 2>&1 | head -n 1
printf 'Private SM-T870 gamescope installed: %s\n' "$binary"
sha256sum "$binary"
