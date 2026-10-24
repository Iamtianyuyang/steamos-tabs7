#!/bin/sh
# Run inside the persistent native Ubuntu rootfs; chronyd owns its lifecycle.
set -eu
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
CONFIG=/etc/chrony/tabs7linux.conf
SOCKET=/run/chrony/chronyd.sock

fail() { printf 'Time sync: %s\n' "$*" >&2; exit 1; }
usage() { printf 'Usage: %s [start|status]\n' "$0"; }
case "${1:-start}" in
    --help|-h) usage; exit 0 ;;
    start|status) action=${1:-start} ;;
    *) usage >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || { usage >&2; exit 2; }
[ "$(id -u)" -eq 0 ] || fail 'run as root inside native Ubuntu'
[ "$(uname -m)" = aarch64 ] || fail 'the native arm64 rootfs is required'
[ -f /.tabs7linux-rootfs ] && [ ! -L /.tabs7linux-rootfs ] || fail 'project rootfs marker is missing'
for record in TABS7LINUX_ROOTFS=1 MODEL=SM-T870 ARCH=arm64 DISTRO=ubuntu-24.04; do
    grep -qx "$record" /.tabs7linux-rootfs || fail 'project rootfs marker does not match'
done
for command in chronyd chronyc pgrep install; do
    command -v "$command" >/dev/null || fail "missing dependency: $command (install chrony first)"
done

# tracking only queries the daemon; status creates no directories or files.
if [ "$action" = status ]; then
    exec chronyc -n -h "$SOCKET" tracking
fi

# Keep any existing daemon and its configuration. An inaccessible command
# socket is an error to inspect, rather than a reason to start a competitor.
if pgrep -x chronyd >/dev/null; then
    printf 'chronyd is already running; keeping the existing daemon.\n'
    exec chronyc -n -h "$SOCKET" tracking
fi
[ -f "$CONFIG" ] && [ ! -L "$CONFIG" ] || fail "missing regular configuration: $CONFIG"
# These are the distribution's runtime/drift locations. The daemon drops to
# Ubuntu's _chrony account and enforces its own standard pidfile check.
install -d -o _chrony -g _chrony -m 0750 /run/chrony /var/lib/chrony
exec chronyd -f "$CONFIG" -l /run/chrony/tabs7linux.log
