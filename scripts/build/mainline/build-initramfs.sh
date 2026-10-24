#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
package="$project_root/out/tools/debs/busybox-static_1.36.1-6ubuntu3.1_arm64.deb"
if [[ ! -f "$package" ]]; then
    echo 'Run scripts/setup/prepare-tools.sh first to download ARM64 BusyBox.' >&2
    exit 1
fi
if [[ $(dpkg-deb -f "$package" Architecture) != arm64 ]]; then
    echo 'BusyBox package must be ARM64.' >&2
    exit 1
fi

workdir=$(mktemp -d)
trap 'rm -rf -- "$workdir"' EXIT
dpkg-deb -x "$package" "$workdir/package"
rootfs="$workdir/rootfs"
mkdir -p "$rootfs/bin" "$rootfs/sbin" "$rootfs/dev" "$rootfs/proc" "$rootfs/sys" "$rootfs/run"
busybox="$workdir/package/usr/bin/busybox"
if [[ ! -f "$busybox" ]]; then busybox="$workdir/package/bin/busybox"; fi
cp "$busybox" "$rootfs/bin/busybox"
chmod 755 "$rootfs/bin/busybox"
cp "$project_root/boot/initramfs/init" "$rootfs/init"
chmod 755 "$rootfs/init"
for applet in sh mount mkdir echo sleep ls head ln ifconfig telnetd setsid dmesg cat ip reboot poweroff; do
    ln -s busybox "$rootfs/bin/$applet"
done
find "$rootfs" -type d -exec chmod 755 {} +
find "$rootfs" -exec touch -h -d '@0' {} +
mkdir -p "$project_root/out/bringup"
(
    cd "$rootfs"
    find . -print0 | LC_ALL=C sort -z | cpio --null --create --format=newc --owner=0:0 --reproducible --quiet
) | gzip -n > "$project_root/out/bringup/initramfs.cpio.gz"
printf 'Initramfs: %s/out/bringup/initramfs.cpio.gz\n' "$project_root"
