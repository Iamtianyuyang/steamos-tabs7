#!/bin/sh
# Switch the BOOT partition between Android and the native Linux/SteamOS image.
#
# Runs inside a custom recovery (TWRP/OrangeFox) shell with root, because the
# bootloader refuses unsigned images over ODIN once its retry counter trips;
# writing the partition directly from recovery is not signature checked.
#
# The images and their hashes are staged by scripts/device/prepare-dual-boot.py
# into $STORE (default: /data/media/0/tabs7linux/boot, visible as
# /sdcard/tabs7linux/boot). Usage inside recovery:
#
#   sh switch-boot.sh status
#   sh switch-boot.sh android [--reboot]
#   sh switch-boot.sh linux   [--reboot]
set -eu

STORE=${TABS7_BOOT_STORE:-/data/media/0/tabs7linux/boot}
reboot_after=no
action=${1:-status}
[ "${2:-}" = "--reboot" ] && reboot_after=yes

say() { printf '%s\n' "$*"; }
die() { printf 'switch-boot: %s\n' "$*" >&2; exit 1; }

find_boot_partition() {
    for candidate in /dev/block/bootdevice/by-name/boot \
                     /dev/block/by-name/boot \
                     /dev/block/platform/soc/1d84000.ufshc/by-name/boot; do
        [ -e "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    return 1
}

partition_bytes() {
    blockdev --getsize64 "$1" 2>/dev/null || \
        printf '%s\n' "$(( $(cat "/sys/class/block/$(basename "$(readlink -f "$1")")/size") * 512 ))"
}

sha_of() { sha256sum "$1" | cut -d' ' -f1; }

expected_sha() {
    # <image>.sha256 holds "hash  filename", written by prepare-dual-boot.py
    [ -f "$STORE/$1.sha256" ] || return 0
    cut -d' ' -f1 < "$STORE/$1.sha256"
}

[ -d "$STORE" ] || die "image store not found: $STORE"
boot_partition=$(find_boot_partition) || die 'BOOT partition not found; is this a recovery with by-name links?'
capacity=$(partition_bytes "$boot_partition")
say "boot partition: $boot_partition ($capacity bytes)"

case "$action" in
status)
    current=$(sha_of "$boot_partition")
    say "current BOOT sha256: $current"
    for target in android linux; do
        [ -f "$STORE/$target.img" ] || { say "$target: not staged"; continue; }
        image=$(sha_of "$STORE/$target.img")
        if [ "$image" = "$current" ]; then
            say "$target: ACTIVE ($image)"
        else
            say "$target: staged ($image)"
        fi
    done
    ;;
android|linux)
    image=$STORE/$action.img
    [ -f "$image" ] || die "$action.img is not staged in $STORE"
    size=$(stat -c %s "$image")
    [ "$size" -eq "$capacity" ] || die "$action.img is $size bytes, partition is $capacity; refusing a partial write"
    want=$(expected_sha "$action")
    [ -n "$want" ] || die "no recorded sha256 for $action ($STORE/$action.sha256 missing)"
    got=$(sha_of "$image")
    [ "$got" = "$want" ] || die "$action.img hash mismatch: $got != $want"
    say "writing $action.img ($got)"
    dd if="$image" of="$boot_partition" bs=1M conv=fsync 2>/dev/null || dd if="$image" of="$boot_partition" bs=1M
    sync
    back=$(sha_of "$boot_partition")
    [ "$back" = "$got" ] || die "readback mismatch: $back != $got"
    say "BOOT written and verified: $back"
    mkdir -p "$STORE"
    printf '%s\n' "$action" > "$STORE/active"
    [ "$reboot_after" = yes ] && { say 'rebooting'; reboot; }
    ;;
*)
    die "usage: switch-boot.sh [status|android|linux] [--reboot]"
    ;;
esac
