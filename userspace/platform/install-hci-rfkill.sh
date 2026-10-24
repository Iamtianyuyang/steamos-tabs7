#!/bin/sh
# Fixed-path privileged HCI software-block helper; no daemon or radio restart.
set -eu
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
SOURCE=${1:-$(dirname "$(dirname "$SCRIPT_DIR")")}
DEST=/usr/local/lib/tabs7linux/gui
[ "$(id -u)" -eq 0 ] || { echo 'Run inside native Ubuntu as root' >&2; exit 1; }
[ "$(id -u linux)" -eq 1000 ]
[ -x /usr/bin/pkexec ]
for file in userspace/platform/hci_rfkill.py userspace/platform/bluetooth/org.tabs7linux.hci-rfkill.policy \
            userspace/platform/bluetooth/49-tabs7-hci-rfkill.rules; do
    [ -r "$SOURCE/$file" ]
done
python3 -I -c 'import os, pathlib, stat
path = pathlib.Path("/usr/local/lib/tabs7linux/gui")
for item in reversed((path, *path.parents)):
    info = item.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise SystemExit("Untrusted HCI helper ancestor: " + str(item))
info = pathlib.Path("/usr/bin/pkexec").stat()
if info.st_uid != 0 or not info.st_mode & stat.S_ISUID:
    raise SystemExit("pkexec must be the root-owned distribution setuid executable")'
mkdir -p /usr/share/polkit-1/actions /etc/polkit-1/rules.d
install -o root -g root -m 0755 "$SOURCE/userspace/platform/hci_rfkill.py" "$DEST/hci_rfkill.py"
install -o root -g root -m 0644 "$SOURCE/userspace/platform/bluetooth/org.tabs7linux.hci-rfkill.policy" \
    /usr/share/polkit-1/actions/org.tabs7linux.hci-rfkill.policy
install -o root -g root -m 0644 "$SOURCE/userspace/platform/bluetooth/49-tabs7-hci-rfkill.rules" \
    /etc/polkit-1/rules.d/49-tabs7-hci-rfkill.rules
printf 'Installed HCI software-block helper and single-action Polkit rule. No radio state changed.\n'
