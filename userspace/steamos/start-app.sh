#!/bin/sh
# Start the Steam session as the only client of the kiosk Sway host.
# Runs as the SteamOS user (UID 1000), inside the SteamOS rootfs.
set -eu

# Debug switch: bring up the display host alone, without gamescope/Steam.
[ -e /etc/tabs7linux/steamos-no-app ] && exit 0

export XDG_RUNTIME_DIR=/run/user/1000
export XDG_CURRENT_DESKTOP=gamescope
export XDG_SESSION_DESKTOP=gamescope
export DESKTOP_SESSION=gamescope
export SteamDeck=1
export STEAM_MULTIPLE_XWAYLANDS=1
export ENABLE_GAMESCOPE_WSI=1
export SDL_VIDEO_MINIMIZE_ON_FOCUS_LOSS=0
export QT_QPA_PLATFORM=xcb

# Wait for the host compositor's socket before nesting inside it.
count=0
while [ ! -S "$XDG_RUNTIME_DIR/wayland-1" ] && [ ! -S "$XDG_RUNTIME_DIR/wayland-0" ]; do
    count=$((count + 1))
    [ "$count" -lt 100 ] || { printf 'no Wayland socket from the host compositor\n' >&2; exit 1; }
    sleep 0.1
done
printf 'gamescope session starting on %s\n' "$XDG_RUNTIME_DIR"

exec gamescope \
    --backend wayland \
    --steam \
    --default-touch-mode 4 \
    --hide-cursor-delay 3000 \
    -W 2560 -H 1600 -w 2560 -h 1600 \
    -- /usr/bin/steam -gamepadui
