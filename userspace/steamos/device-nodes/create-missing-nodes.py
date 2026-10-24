#!/usr/bin/env python3
"""Create /dev/input nodes on a kernel without DEVTMPFS.

The vendor 4.19 kernel has no DEVTMPFS, so /dev is a plain tmpfs: systemd-udevd
cannot rely on the kernel creating nodes, and in practice a gamepad created by
Steam Input (uinput), a Bluetooth controller or a re-probed digitizer never
appears as /dev/input/eventN. libinput then has nothing to open, which is what
blocked Steam Input on this device.

This service watches /sys/class/input, creates the missing nodes itself and
replays the "add" uevent so udev picks the device up as if the kernel had
announced it. Nodes it created are removed again when the device disappears.

    create-missing-nodes.py                 # run until stopped (systemd)
    create-missing-nodes.py --once          # single pass, for tests
    create-missing-nodes.py --sysfs DIR --dev DIR --once   # dry layout check
"""
import argparse
import grp
import os
import pwd
import stat
import sys
import time
from pathlib import Path

DEV_INPUT = Path('/dev/input')
SYSFS_INPUT = Path('/sys/class/input')
NODE_MODE = 0o660


def plan(sysfs, dev):
    """Return (creates, stale) without touching anything.

    creates: [(node_path, rdev, event_name)] for nodes that must be created
    stale:   [node_path] devtmpfs-less leftovers whose device is gone
    """
    creates, present = [], set()
    for entry in sorted(Path(sysfs).glob('event*')):
        devfile = entry / 'dev'
        if not devfile.is_file():
            continue
        try:
            major, minor = (int(part) for part in devfile.read_text().strip().split(':'))
        except ValueError:
            continue
        node = Path(dev) / entry.name
        present.add(node)
        rdev = os.makedev(major, minor)
        try:
            current = node.stat().st_rdev
        except FileNotFoundError:
            creates.append((node, rdev, entry.name))
            continue
        if current != rdev:
            creates.append((node, rdev, entry.name))
    stale = []
    if Path(dev).is_dir():
        for node in Path(dev).iterdir():
            if node.name.startswith('event') and node not in present:
                stale.append(node)
    return creates, stale


def input_group():
    try:
        return grp.getgrnam('input').gr_gid
    except KeyError:
        try:
            return pwd.getpwnam('root').pw_gid
        except KeyError:
            return 0


def apply(creates, stale, sysfs, log):
    gid = input_group()
    for node, rdev, name in creates:
        node.parent.mkdir(parents=True, exist_ok=True)
        if node.exists():
            node.unlink()
        os.mknod(node, stat.S_IFCHR | NODE_MODE, rdev)
        os.chown(node, 0, gid)
        log(f'created {node} ({os.major(rdev)}:{os.minor(rdev)}) for {name}')
        uevent = Path(sysfs) / name / 'uevent'
        if uevent.is_file():
            with uevent.open('w') as handle:
                handle.write('add')
            log(f'replayed add uevent for {name}')
    for node in stale:
        try:
            node.unlink()
            log(f'removed {node} (device gone)')
        except FileNotFoundError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sysfs', type=Path, default=SYSFS_INPUT)
    parser.add_argument('--dev', type=Path, default=DEV_INPUT)
    parser.add_argument('--interval', type=float, default=0.5)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    def log(message):
        print(f'{time.strftime("%H:%M:%S")} {message}', flush=True)

    while True:
        try:
            creates, stale = plan(args.sysfs, args.dev)
            if creates or stale:
                apply(creates, stale, args.sysfs, log)
        except OSError as error:
            log(f'error: {error}')
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
