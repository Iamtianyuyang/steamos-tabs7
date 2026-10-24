#!/bin/sh
# Install Valve's native linuxarm64 Steam client into a SteamOS home.
#
# Install the native public-beta arm64 build. Archive integrity must already
# have been checked against Valve's downloaded manifest.
#
# Usage: install-steam-arm64.sh <client.zip> [steam-home [cef.zip sdl.zip ...]]
#   client.zip  bins_linuxarm64_linuxarm64.zip from client-update.steamstatic.com
#   home        SteamOS user home (default /home/steamos)
set -eu

zip=${1:?usage: install-steam-arm64.sh <client.zip> [home]}
steam_home=${2:-/home/steamos}
root=$steam_home/.local/share/Steam
self_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() { printf 'Steam install: %s\n' "$*" >&2; exit 1; }

[ -f "$zip" ] || fail "missing client archive: $zip"
[ -d "$steam_home" ] || fail "missing home directory: $steam_home"

if [ "$#" -gt 0 ]; then shift; fi
if [ "$#" -gt 0 ]; then shift; fi
python3 "$self_dir/extract-steam-arm64.py" "$zip" "$root" "$@"
[ -x "$root/steamrtarm64/steam" ] || fail 'archive does not contain steamrtarm64/steam'

mkdir -p "$root/package"
printf 'publicbeta\n' > "$root/package/beta"

# These are different runtime payloads in Valve's linuxarm64 manifest. Old
# project installers aliased them and the updater overwrote one with another.
# Remove only that exact obsolete link; keep real directories and other links.
for alias in steamrt64 linuxarm64; do
    if [ -L "$root/$alias" ] && [ "$(readlink "$root/$alias")" = steamrtarm64 ]; then
        rm "$root/$alias"
    fi
done
: > "$root/.steam-enable-steamrt64-client"

# Keep the official updater and its checksum validation enabled. Existing
# configuration is retained, including any user-managed update policy.

# SteamOS asks steamos-manager for iwd; this kernel uses NetworkManager.
mkdir -p "$root/config"
if [ ! -e "$root/config/config.vdf" ]; then
cat > "$root/config/config.vdf" <<'EOF'
"InstallConfigStore"
{
	"SteamOS"
	{
		"WifiForceWPASupplicant"		"1"
	}
}
EOF
fi

# Without a registry the first run skips setup; keep OOBE pending.
mkdir -p "$steam_home/.steam"
if [ ! -e "$steam_home/.steam/registry.vdf" ]; then
cat > "$steam_home/.steam/registry.vdf" <<'EOF'
"Registry"
{
    "HKCU"
    {
        "Software"
        {
            "Valve"
            {
                "Steam"
                {
                    "CompletedOOBEStage1" "0"
                    "CompletedOOBE" "0"
                }
            }
        }
    }
}
EOF
fi
for alias in steam root; do
    if [ ! -e "$steam_home/.steam/$alias" ] && [ ! -L "$steam_home/.steam/$alias" ]; then
        ln -s "$root" "$steam_home/.steam/$alias"
    fi
done

mkdir -p "$root/steamapps" "$root/userdata"
chown -R 1000:1000 "$root/steamrtarm64"
chown 1000:1000 "$steam_home/.local" "$steam_home/.local/share" "$root" \
    "$root/package" "$root/package/beta" "$root/config" "$root/config/config.vdf" \
    "$root/steamapps" "$root/userdata" "$root/.steam-enable-steamrt64-client" \
    "$steam_home/.steam" "$steam_home/.steam/registry.vdf"

printf 'Steam arm64 client installed: %s\n' "$root"
