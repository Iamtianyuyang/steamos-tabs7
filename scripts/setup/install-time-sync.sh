#!/bin/sh
# Install files only; install the distribution chrony package separately.
set -eu
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
usage() { printf 'Usage: %s [PROJECT_ROOT]\n' "$0"; }
case "${1:-}" in --help|-h) usage; exit 0 ;; esac
[ "$#" -le 1 ] || { usage >&2; exit 2; }
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
PROJECT=${1:-$(dirname "$(dirname "$SCRIPT_DIR")")}
SOURCE=$PROJECT/userspace/services/time
DEST=/usr/local/lib/tabs7linux/time
fail() { printf 'Time sync install: %s\n' "$*" >&2; exit 1; }
[ "$(id -u)" -eq 0 ] || fail 'run as root inside native Ubuntu'
[ "$(uname -m)" = aarch64 ] || fail 'the native arm64 rootfs is required'
[ -f /.tabs7linux-rootfs ] && [ ! -L /.tabs7linux-rootfs ] || fail 'project rootfs marker is missing'
for record in TABS7LINUX_ROOTFS=1 MODEL=SM-T870 ARCH=arm64 DISTRO=ubuntu-24.04; do
    grep -qx "$record" /.tabs7linux-rootfs || fail 'project rootfs marker does not match'
done
for command in chronyd chronyc pgrep install; do
    command -v "$command" >/dev/null || fail "missing dependency: $command (install chrony first)"
done
[ -f "$SOURCE/chrony.conf" ] && [ -f "$SOURCE/start-time-sync.sh" ] || fail 'service source files are missing'
chronyd -p -f "$SOURCE/chrony.conf" >/dev/null
sh -n "$SOURCE/start-time-sync.sh"
install -d -m 0755 /etc/chrony "$DEST"
install -m 0644 "$SOURCE/chrony.conf" /etc/chrony/tabs7linux.conf
install -m 0755 "$SOURCE/start-time-sync.sh" "$DEST/start-time-sync.sh"
printf 'Installed native time sync files; no daemon was started or restarted.\n'
