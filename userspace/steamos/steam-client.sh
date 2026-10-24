#!/bin/sh
# Native ARM64 Steam client inside the separate SteamOS userspace.
# The distro /usr/bin/steam stub points to a missing /usr/lib/steam/steam.
set -eu
[ "$(id -u)" -ne 0 ] || { echo 'Run Steam as the SteamOS user' >&2; exit 1; }
case "$(uname -m)" in aarch64|arm64) ;; *) echo 'Native ARM64 is required' >&2; exit 1 ;; esac
[ "$(sed -n 's/^ID=//p' /usr/lib/os-release)" = steamos ] || { echo 'Run inside SteamOS' >&2; exit 1; }
steam_root=${HOME:?}/.local/share/Steam
steam_client=$steam_root/steamrtarm64/steam
[ -x "$steam_client" ] || { echo 'Install the native ARM64 Steam client first' >&2; exit 1; }
export LD_LIBRARY_PATH="$steam_root/steamrtarm64:$steam_root/steamrtarm64/libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
cd "$steam_root"
[ "$#" -gt 0 ] || set -- -gamepadui
while :; do
    status=0
    "$steam_client" "$@" || status=$?
    [ "$status" -eq 42 ] || exit "$status"
    printf 'Steam requested restart after updating; relaunching native ARM64 client\n' >&2
    sleep 1
done
