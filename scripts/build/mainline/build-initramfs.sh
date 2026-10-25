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
cp "$project_root/boot/initramfs/start-persistent-gui" "$rootfs/sbin/start-persistent-gui"
chmod 755 "$rootfs/init" "$rootfs/sbin/start-persistent-gui"
firmware="$project_root/out/firmware/gts7lwifi"
for name in a650_sqe.fw a650_gmu.bin a650_zap.mdt a650_zap.b00 a650_zap.b01 a650_zap.b02; do
    [[ -f "$firmware/$name" ]] || { echo "Missing GPU firmware: $firmware/$name" >&2; exit 1; }
done
mkdir -p "$rootfs/lib/firmware/qcom/sm8250/gts7lwifi"
cp "$firmware/a650_sqe.fw" "$firmware/a650_gmu.bin" "$rootfs/lib/firmware/qcom/"
cp "$firmware/a650_zap.mdt" "$firmware/a650_zap.b00" "$firmware/a650_zap.b01" "$firmware/a650_zap.b02" "$rootfs/lib/firmware/qcom/sm8250/gts7lwifi/"
# ADSP (speaker path): the PAS remoteproc boots it at probe, before the
# SteamOS root exists, so the split image must be in the initramfs. Files are
# from NON-HLOS image/; extracted and hash-checked into out/ (audio-6.18.md).
adsp="$firmware/audio/adsp"
(cd "$adsp" && sha256sum --quiet -c <(grep ' adsp/adsp\.' ../SHA256SUMS | sed 's| adsp/| |')) ||
    { echo "ADSP firmware missing or changed: $adsp" >&2; exit 1; }
cp "$adsp"/adsp.mdt "$adsp"/adsp.b[0-9][0-9] "$rootfs/lib/firmware/qcom/sm8250/gts7lwifi/"
for applet in sh mount mkdir echo sleep ls head tail grep ln readlink ifconfig telnetd setsid dmesg cat ip i2cget i2cset i2ctransfer reboot poweroff; do
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
