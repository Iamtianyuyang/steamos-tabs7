#!/usr/bin/env python3
"""Stage a BOOT image on the running SteamOS over RNDIS and optionally write it.

ODIN sessions break after a failed custom boot and TWRP needs a reboot; the
device itself is reachable at 192.168.77.2:23 (debug shell) with root, python3
and `/dev/disk/by-partlabel/boot`. This script uses that: it starts a tiny TCP
receiver on the device, streams the image, verifies the hash there, and (with
--write) dd's it to the BOOT partition and compares the readback.

    scripts/device/push-boot.py --image out/steamos-kernel-6.2/boot2/boot.img --verify
    scripts/device/push-boot.py --image out/steamos-kernel-6.2/boot2/boot.img --write

Every step prints progress; keep each call bounded (image transfer is a few
seconds on RNDIS).
"""
import argparse
import base64
import hashlib
import re
import socket
import sys
import time
from pathlib import Path

DEVICE = '192.168.77.2'
SHELL_PORT = 23
DATA_PORT = 7000
IMAGE_PATH = '/tmp/tabs7-boot.img'
LOG_PATH = '/tmp/tabs7-push.log'

RECEIVER = '''
import socket, sys
port = %d
target = "%s"
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("0.0.0.0", port))
server.listen(1)
server.settimeout(60)
conn, _ = server.accept()
with open(target, "wb") as out:
    while True:
        chunk = conn.recv(1 << 20)
        if not chunk:
            break
        out.write(chunk)
conn.close()
server.close()
''' % (DATA_PORT, IMAGE_PATH)


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def shell(command, wait=1.5, quiet=1.0):
    """Run one command in a fresh device shell session and return its output.

    The session is drained with a short timeout so a call never blocks for long
    just because telnetd keeps the socket open.
    """
    with socket.create_connection((DEVICE, SHELL_PORT), timeout=10) as sock:
        sock.settimeout(quiet)
        sock.sendall(command.encode() + b'\n')
        time.sleep(wait)
        chunks = []
        while True:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
    return b''.join(chunks).decode('utf-8', 'replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--write', action='store_true',
                        help='dd the staged image to /dev/disk/by-partlabel/boot after verifying')
    parser.add_argument('--verify', action='store_true', help='transfer and verify only (default)')
    args = parser.parse_args()

    image = Path(args.image).resolve()
    data = image.read_bytes()
    digest = sha_bytes(data)
    print(f'image    : {image.name} {len(data)} bytes {digest}')

    sizes = shell('echo CAP=$(cat /sys/class/block/$(basename $(readlink -f '
                  '/dev/disk/by-partlabel/boot))/size)', wait=1.5)
    match = re.search(r'CAP=(\d+)', sizes)
    if not match:
        raise SystemExit(f'cannot read the BOOT partition size: {sizes[-120:]!r}')
    capacity = int(match.group(1)) * 512
    print(f'partition: {capacity} bytes')
    if len(data) != capacity:
        raise SystemExit(f'image is {len(data)} bytes, BOOT partition is {capacity}; refusing')

    runner = ('#!/bin/sh\npython3 /tmp/tabs7-recv.py\n'
              f'echo "done:$(sha256sum {IMAGE_PATH})" > {LOG_PATH}\n')
    receiver_b64 = base64.b64encode(RECEIVER.encode()).decode()
    runner_b64 = base64.b64encode(runner.encode()).decode()
    shell(f'printf %s "{receiver_b64}" | base64 -d > /tmp/tabs7-recv.py; '
          f'printf %s "{runner_b64}" | base64 -d > /tmp/tabs7-run.sh; '
          f'chmod 755 /tmp/tabs7-run.sh; rm -f {LOG_PATH}', wait=3)
    check = shell('ls -l /tmp/tabs7-recv.py /tmp/tabs7-run.sh 2>&1', wait=1.5)
    if 'tabs7-run.sh' not in check or 'No such' in check:
        raise SystemExit(f'cannot stage the receiver: {check[-160:]!r}')
    shell('setsid sh /tmp/tabs7-run.sh </dev/null >/dev/null 2>&1 &', wait=2)
    time.sleep(1.5)

    sent = 0
    with socket.create_connection((DEVICE, DATA_PORT), timeout=15) as sock:
        sock.settimeout(30)
        view = memoryview(data)
        while sent < len(data):
            sent += sock.send(view[sent:sent + (1 << 20)])
    print(f'transfer : {sent} bytes sent')

    for _ in range(20):
        time.sleep(1)
        log = shell(f'cat {LOG_PATH} 2>/dev/null', wait=1)
        if 'done:' in log:
            break
    received = log.split('done:')[-1].strip().split()[0] if 'done:' in log else ''
    print(f'device   : {received}')
    if received != digest:
        raise SystemExit('device hash mismatch; image not written')

    if not args.write:
        print('verified : staged, not written (pass --write to flash)')
        return

    write = (f'dd if={IMAGE_PATH} of=/dev/disk/by-partlabel/boot bs=1M conv=fsync && sync && '
             f'echo BACK=$(sha256sum /dev/disk/by-partlabel/boot | cut -d" " -f1)')
    out = shell(write, wait=10)
    match = re.search(r'BACK=([0-9a-f]{64})', out)
    back = match.group(1) if match else ''
    print(f'readback : {back or "not found in output"}')
    if back != digest:
        raise SystemExit('readback mismatch after write')
    print('written  : BOOT now matches the image')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, socket.timeout) as error:
        sys.exit(f'push-boot failed: {error}')
