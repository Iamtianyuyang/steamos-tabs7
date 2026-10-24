#!/usr/bin/env python3
"""Bounded device state polling (never blocks longer than two minutes).

Long single commands are not observably reliable in this environment, so every
wait is a separate, short invocation of this helper instead of a shell loop:

    scripts/device/wait-for-device.py download          # Samsung ODIN mode
    scripts/device/wait-for-device.py gadget            # native Linux RNDIS
    scripts/device/wait-for-device.py gadget --timeout 60

States: download (04e8:685d), gadget (1d6b:0104), ping (192.168.77.2 answers),
adb (an ADB device is listed). Exits 0 when the requested state is reached and 1
on timeout, so callers can chain steps in short bursts.
"""
import argparse
import subprocess
import sys
import time
import socket

STATES = ('download', 'gadget', 'ping', 'adb')
MAX_TIMEOUT = 120  # hard cap: no invocation may block longer than two minutes


def usb_ids():
    try:
        output = subprocess.run(['lsusb'], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split()[5] for line in output.splitlines() if len(line.split()) > 5}


def ping(host='192.168.77.2', timeout=1.0):
    try:
        with socket.create_connection((host, 23), timeout=timeout):
            return True
    except OSError:
        return False


def adb():
    try:
        output = subprocess.run(['adb', 'devices'], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip() and not line.startswith('List of') for line in output.splitlines()[1:])


def observe():
    ids = usb_ids()
    return {'download': '04e8:685d' in ids,
            'gadget': '1d6b:0104' in ids,
            'ping': ping(),
            'adb': adb()}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('state', choices=STATES)
    parser.add_argument('--timeout', type=float, default=100,
                        help=f'seconds to wait, capped at {MAX_TIMEOUT} (default 100)')
    parser.add_argument('--interval', type=float, default=3, help='poll interval (default 3 s)')
    args = parser.parse_args()

    deadline = time.monotonic() + min(args.timeout, MAX_TIMEOUT)
    while True:
        seen = observe()
        flags = ' '.join(f'{name}={int(value)}' for name, value in seen.items())
        print(f'{time.strftime("%H:%M:%S")} {flags}', flush=True)
        if seen[args.state]:
            print(f'reached: {args.state}')
            return 0
        if time.monotonic() >= deadline:
            print(f'timeout: {args.state} not reached', flush=True)
            return 1
        time.sleep(args.interval)


if __name__ == '__main__':
    sys.exit(main())
