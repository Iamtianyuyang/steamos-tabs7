#!/usr/bin/python3 -I
"""Read or change only the SM-T870 ttyHS0 HCI software rfkill node.

The privileged entry point accepts exactly 0 (unblock) or 1 (block). It never
opens /dev/rfkill or the board bt_power GPIO, and never closes the H4 UART.
"""
import json
import os
from pathlib import Path
import re
import sys

HELPER = "/usr/local/lib/tabs7linux/gui/hci_rfkill.py"
HCI_DEVICE = "devices/platform/soc/998000.qcom,qup_uart/tty/ttyHS0/hci0"


def bit_at(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        value = os.read(fd, 16).decode("ascii").strip()
    finally:
        os.close(fd)
    if value not in ("0", "1"):
        raise RuntimeError(f"invalid HCI rfkill {name}")
    return value == "1"


def text_at(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
    try:
        return os.read(fd, 64).decode("ascii").strip()
    finally:
        os.close(fd)


def open_node(sys_root=Path("/sys")):
    """Pin the verified physical directory; a changing/ambiguous node fails."""
    sys_root = Path(sys_root)
    expected = sys_root / HCI_DEVICE
    if (sys_root / "class/bluetooth/hci0").resolve(strict=True) != expected:
        raise RuntimeError("hci0 is not the expected SM-T870 ttyHS0 controller")
    matches = []
    for path in (sys_root / "class/rfkill").glob("rfkill*"):
        if not re.fullmatch(r"rfkill[0-9]+", path.name):
            continue
        resolved = path.resolve(strict=True)
        fd = os.open(resolved, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            name, kind = text_at(fd, "name"), text_at(fd, "type")
            if name == "hci0":
                if kind != "bluetooth" or resolved.parent != expected or resolved.name != path.name:
                    raise RuntimeError("unexpected hci0 rfkill topology or type")
                matches.append((resolved, os.fstat(fd)))
        finally:
            os.close(fd)
    if len(matches) != 1:
        raise RuntimeError("expected exactly one ttyHS0 hci0 Bluetooth rfkill node")
    path, found = matches[0]
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        current = os.fstat(fd)
        if ((current.st_dev, current.st_ino) != (found.st_dev, found.st_ino)
                or text_at(fd, "name") != "hci0" or text_at(fd, "type") != "bluetooth"):
            raise RuntimeError("HCI rfkill device changed while opening it")
    except BaseException:
        os.close(fd)
        raise
    return fd, path


def state_from_fd(fd, path):
    info = os.fstat(fd)
    return dict(path=str(path), identity=[info.st_dev, info.st_ino],
                soft=bit_at(fd, "soft"), hard=bit_at(fd, "hard"))


def read_state(sys_root=Path("/sys")):
    fd, path = open_node(sys_root)
    try:
        return state_from_fd(fd, path)
    finally:
        os.close(fd)


def set_blocked(blocked, sys_root=Path("/sys")):
    if type(blocked) is not bool:
        raise ValueError("HCI rfkill state must be a boolean")
    fd, path = open_node(sys_root)
    try:
        before = state_from_fd(fd, path)
        if before["soft"] != blocked:
            output = os.open("soft", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            try:
                data = b"1\n" if blocked else b"0\n"
                if os.write(output, data) != len(data):
                    raise RuntimeError("short HCI rfkill write")
            finally:
                os.close(output)
        result = state_from_fd(fd, path)
        fresh = read_state(sys_root)
        if fresh != result or result["soft"] != blocked:
            raise RuntimeError("HCI rfkill write/readback did not confirm the requested state")
        return result
    finally:
        os.close(fd)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("0", "1"):
        raise RuntimeError("usage: hci_rfkill.py 0|1")
    if os.geteuid() != 0:
        raise RuntimeError("HCI software rfkill writes require root")
    print(json.dumps(set_blocked(sys.argv[1] == "1"), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"HCI software rfkill failed: {error}", file=sys.stderr)
        sys.exit(1)
