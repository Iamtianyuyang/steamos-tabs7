#!/usr/bin/env python3
"""Rebuild the native Linux initramfs with the SteamOS hand-over helper.

Reuses the pinned static BusyBox and deterministic newc writer of
build-vendor-linux.py, then compares every archive entry against the ramdisk
of the working image: only init and sbin/start-persistent-gui may change.
Offline only; never accesses a device.
"""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]
CHANGEABLE = {'init', 'sbin/start-persistent-gui'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def entries(archive):
    """Parse newc into {name: (mode, rdevmajor, rdevminor, content)}."""
    result, offset = {}, 0
    while True:
        if archive[offset:offset + 6] != b'070701':
            raise ValueError(f'Not a newc archive at offset {offset}')
        fields = [int(archive[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        mode, size, major, minor, name_size = fields[1], fields[6], fields[9], fields[10], fields[11]
        start = offset + 110
        name = archive[start:start + name_size - 1].decode()
        start += name_size
        start += -start % 4
        content = archive[start:start + size]
        offset = start + size
        offset += -offset % 4
        if name == 'TRAILER!!!':
            return result
        if name in result:
            raise ValueError('Duplicate archive entry: ' + name)
        result[name] = (mode, major, minor, content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True,
                        help='gzip ramdisk unpacked from the working image')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('vendor_builder', ROOT / 'scripts/build/vendor/build-vendor-linux.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    package = ROOT / 'out/tools/debs' / builder.BUSYBOX_PACKAGE
    if sha(package.read_bytes()) != builder.BUSYBOX_PACKAGE_SHA256:
        raise ValueError('BusyBox package hash mismatch')
    payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)])
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as package_tar:
        busybox = package_tar.extractfile('./usr/bin/busybox').read()
    if sha(busybox) != builder.BUSYBOX_SHA256:
        raise ValueError('BusyBox binary hash mismatch')

    scripts = {}
    for name, path in (('init', 'boot/initramfs/init-vendor-linux'),
                       ('sbin/start-persistent-gui', 'boot/initramfs/start-persistent-gui')):
        subprocess.run(['sh', '-n', str(ROOT / path)], check=True)
        scripts[name] = (ROOT / path).read_bytes()
    archive = builder.newc_archive(busybox, scripts['init'], scripts['sbin/start-persistent-gui'])

    reference_gz = args.reference.read_bytes()
    old, new = entries(gzip.decompress(reference_gz)), entries(archive)
    if set(old) != set(new):
        raise ValueError(f'Archive entry set changed: {sorted(set(old) ^ set(new))}')
    changed = sorted(name for name in old if old[name] != new[name])
    if not set(changed) <= CHANGEABLE:
        raise ValueError(f'Entries outside the reviewed scripts changed: {changed}')
    for name in changed:
        if old[name][:3] != new[name][:3]:
            raise ValueError(f'Mode or device numbers changed: {name}')

    out = args.output.resolve()
    if not out.is_relative_to(ROOT / 'out'):
        raise ValueError('Build artifacts must stay under project out/')
    out.mkdir(parents=True, exist_ok=True)
    ramdisk = gzip.compress(archive, compresslevel=9, mtime=0)
    (out / 'initramfs.cpio.gz').write_bytes(ramdisk)
    review = {
        'kind': 'initramfs script update', 'reference_sha256': sha(reference_gz),
        'ramdisk_sha256': sha(ramdisk), 'ramdisk_bytes': len(ramdisk),
        'changed_entries': {name: {'before_sha256': sha(old[name][3]), 'after_sha256': sha(new[name][3])}
                            for name in changed},
        'unchanged_entries': len(old) - len(changed), 'busybox_sha256': builder.BUSYBOX_SHA256,
        'device_write_performed': False,
    }
    (out / 'initramfs-review.json').write_text(json.dumps(review, indent=2) + '\n')
    print(json.dumps(review, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f'Initramfs build failed: {error}')
