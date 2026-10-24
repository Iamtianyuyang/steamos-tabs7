#!/usr/bin/env python3
"""Flash a BOOT image from a running custom recovery and report how it boots.

One experiment per invocation: push the image, dd it to the BOOT partition,
compare the readback, reboot with `twrp reboot`, then sample USB for a bounded
time to see whether the image reaches the initramfs (RNDIS gadget) or whether
the bootloader drops into Download mode.

    scripts/device/flash-via-recovery.py --image out/.../boot.img

Needs the device booted into TWRP/PBRP with adb. Every step is bounded; a
failed boot leaves the device in Download mode and needs a manual power cycle
plus the recovery key combo, so the outcome is reported explicitly.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

BOOT = '/dev/block/bootdevice/by-name/boot'
STORE = '/data/tabs7linux/boot'


def adb(*arguments, timeout=60):
    result = subprocess.run(['adb', *arguments], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise SystemExit(f'adb {" ".join(arguments)} failed: {result.stderr.strip() or result.stdout.strip()}')
    return result.stdout


def usb_state():
    out = subprocess.run(['lsusb'], capture_output=True, text=True).stdout
    tags = []
    if '04e8:685d' in out:
        tags.append('DOWNLOAD')
    if '1d6b:0104' in out:
        tags.append('gadget')
    return ','.join(tags) or 'nothing'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--watch', type=float, default=70, help='seconds to sample USB after reboot')
    args = parser.parse_args()

    image = Path(args.image).resolve()
    local_sha = subprocess.run(['sha256sum', str(image)], capture_output=True, text=True).stdout.split()[0]
    print(f'image    : {image.name} {image.stat().st_size} bytes {local_sha}')

    adb('shell', f'mkdir -p {STORE}')
    adb('push', str(image), f'{STORE}/experiment.img', timeout=120)
    written = adb('shell', f'dd if={STORE}/experiment.img of={BOOT} bs=1M conv=fsync 2>/dev/null; '
                           f'sync; sha256sum {BOOT}').strip().split()[0]
    print(f'readback : {written}')
    if written != local_sha:
        raise SystemExit('readback does not match the image; not rebooting')

    adb('shell', 'twrp reboot', timeout=20)
    print('rebooted : sampling USB')

    start = time.time()
    previous = None
    while time.time() - start < args.watch:
        state = usb_state()
        if state != previous:
            print(f'+{time.time() - start:4.0f}s  {state}', flush=True)
            previous = state
        if state == 'gadget':
            print('result   : initramfs reached USB (kernel executed)')
            return 0
        if state == 'DOWNLOAD' and time.time() - start > 10:
            print('result   : bootloader dropped to Download mode (image not executed)')
            return 1
        time.sleep(2)
    print(f'result   : no verdict within {args.watch:.0f}s (last state: {previous})')
    return 2


if __name__ == '__main__':
    sys.exit(main())
