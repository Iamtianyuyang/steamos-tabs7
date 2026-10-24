#!/bin/sh
# Install source controls around already verified native Bluetooth assets.
set -eu
SCRIPT_DIR=$(dirname "$(readlink -f "$0")")
SOURCE=${1:-$(dirname "$(dirname "$SCRIPT_DIR")")}
DEST=/usr/local/lib/tabs7linux
[ "$(id -u)" -eq 0 ] || { echo 'Run inside native Ubuntu as root' >&2; exit 1; }
for file in start-bluetooth.py bluetooth/bootstrap-qca6390-h4.py bluetooth/probe-ttyhs0-version.py; do
    [ -r "$SOURCE/userspace/platform/$file" ]
done
[ -r "$DEST/bluetooth/module/tabs7_hci_uart.ko" ]
[ -r "$DEST/bluetooth/firmware/htbtfw20.tlv" ]
[ -r "$DEST/bluetooth/firmware/htnv20.bin" ]
[ -r /etc/tabs7linux/bluetooth/bt_addr ]
install -m 0755 "$SOURCE/userspace/platform/start-bluetooth.py" "$DEST/gui/start-bluetooth.py"
install -m 0700 "$SOURCE/userspace/platform/bluetooth/bootstrap-qca6390-h4.py" "$DEST/bluetooth/bootstrap-qca6390-h4.py"
install -m 0700 "$SOURCE/userspace/platform/bluetooth/probe-ttyhs0-version.py" "$DEST/bluetooth/probe-ttyhs0-version.py"
sh "$SOURCE/userspace/platform/install-hci-rfkill.sh" "$SOURCE"
python3 "$DEST/gui/start-bluetooth.py" start
python3 "$DEST/gui/start-bluetooth.py" status
printf 'Enable automatic startup only after hardware pairing validation: /etc/tabs7linux/bluetooth/enabled\n'
