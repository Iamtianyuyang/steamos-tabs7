#!/usr/bin/env python3
"""Build a mainline-kernel recovery candidate that keeps the vendor DTB.

This is the discriminating experiment for the mainline bring-up: the only
variable against the working vendor image is the kernel payload.  If the
device still falls into Samsung Download mode, the bootloader is rejecting
the mainline kernel itself and the DTB is not the problem.

The image is packed with the exact header values dumped from the working
vendor recovery image, then AVB-signed the same way as the reference.
"""

import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--dtb', type=Path)
    parser.add_argument('--dtbo', type=Path)
    parser.add_argument('--initramfs', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--cmdline')
    parser.add_argument('--partition', default='RECOVERY')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    artifacts = root / 'out/bringup'
    tools = root / 'out/tools'
    vendor = root / 'out/vendor-linux/reference'
    kernel = args.kernel or artifacts / 'Image'
    dtb = args.dtb or vendor / 'dtb'
    dtbo = args.dtbo or vendor / 'recovery_dtbo'
    initramfs = args.initramfs or artifacts / 'initramfs.cpio.gz'
    reference = args.reference or root / 'out/vendor-linux/recovery.img'
    output = (args.output or artifacts / 'recovery-mainline-vendordtb.img').resolve()

    for path in (kernel, dtb, dtbo, initramfs, reference):
        if not path.is_file():
            raise SystemExit(f'missing input: {path}')

    # Header values come from the image that already boots on this tablet.
    info = subprocess.check_output(
        [sys.executable, str(tools / 'mkbootimg/unpack_bootimg.py'),
         '--boot_img', str(reference), '--out', '/tmp/tabs7-vendor-header'],
        text=True)
    values = {}
    for line in info.splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        values[key.strip()] = value.strip()

    cmdline = args.cmdline or values['command line args']
    if 'rdinit=/init' not in cmdline:
        cmdline += ' rdinit=/init'
    print(f'kernel     {kernel} ({kernel.stat().st_size} bytes)')
    print(f'dtb        {dtb}')
    print(f'dtbo       {dtbo}')
    print(f'cmdline    {cmdline}')

    packed = Path('/tmp/tabs7-mainline-vendordtb.img')
    subprocess.run([
        sys.executable, str(tools / 'mkbootimg/mkbootimg.py'),
        '--kernel', str(kernel),
        '--ramdisk', str(initramfs),
        '--dtb', str(dtb),
        '--recovery_dtbo', str(dtbo),
        '--cmdline', cmdline,
        '--kernel_offset', values['kernel load address'],
        '--ramdisk_offset', values['ramdisk load address'],
        '--tags_offset', values['kernel tags load address'],
        '--dtb_offset', values['dtb address'],
        '--header_version', '2',
        '--pagesize', values['page size'],
        '--os_version', values['os version'],
        '--os_patch_level', values['os patch level'],
        '--output', str(packed),
    ], check=True)

    # Round-trip every component before signing.
    unpacked = Path('/tmp/tabs7-mainline-unpacked')
    if unpacked.exists():
        shutil.rmtree(unpacked)
    subprocess.run([sys.executable, str(tools / 'mkbootimg/unpack_bootimg.py'),
                    '--boot_img', str(packed), '--out', str(unpacked)], check=True,
                   stdout=subprocess.DEVNULL)
    for component, source in (('kernel', kernel), ('dtb', dtb),
                              ('recovery_dtbo', dtbo), ('ramdisk', initramfs)):
        if (unpacked / component).read_bytes() != source.read_bytes():
            raise SystemExit(f'repack mismatch: {component}')

    profile = json.loads(
        (root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    partition = args.partition.upper()
    capacity = profile['device_pit']['partitions'][partition]['capacity_bytes']
    if packed.stat().st_size > capacity:
        raise SystemExit(f'candidate exceeds RECOVERY capacity: {packed.stat().st_size} > {capacity}')

    subprocess.run([sys.executable, str(root / 'scripts/setup/prepare-avb-tools.py')], check=True)
    avb = [sys.executable, str(tools / 'avb/avbtool.py')]
    key = tools / 'avb/testkey_rsa4096.pem'
    output.parent.mkdir(parents=True, exist_ok=True)
    # avbtool resolves the hash descriptor's partition name relative to the
    # working directory, so sign a file literally called recovery.img.
    workspace = output.parent / 'avb-mainline'
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    work_image = workspace / f'{partition.lower()}.img'
    shutil.copyfile(packed, work_image)
    subprocess.run(avb + ['add_hash_footer', '--image', str(work_image),
                          '--partition_size', str(capacity),
                          '--partition_name', partition.lower(),
                          '--algorithm', 'SHA256_RSA4096',
                          '--key', str(key),
                          '--rollback_index', '1790812800',
                          '--rollback_index_location', '0'], check=True, cwd=workspace)
    subprocess.run(avb + ['verify_image', '--image', str(work_image), '--key', str(key)],
                   check=True, cwd=workspace)
    shutil.copyfile(work_image, output)

    manifest = {
        'kind': 'mainline kernel + vendor DTB / DTBO, AVB-signed experiment',
        'kernel': {'path': str(kernel), 'sha256': hashlib.sha256(kernel.read_bytes()).hexdigest()},
        'dtb': {'path': str(dtb), 'sha256': hashlib.sha256(dtb.read_bytes()).hexdigest()},
        'dtbo': {'path': str(dtbo), 'sha256': hashlib.sha256(dtbo.read_bytes()).hexdigest()},
        'initramfs_sha256': hashlib.sha256(initramfs.read_bytes()).hexdigest(),
        'cmdline': cmdline,
        'image': output.name,
        'size': output.stat().st_size,
        'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'partition_capacity': capacity,
        'native_boot_tested': False,
    }
    (artifacts / 'recovery-mainline-vendordtb.json').write_text(
        json.dumps(manifest, indent=2) + '\n')
    print(f'candidate {output} ({output.stat().st_size} bytes)')
    print(f'sha256    {manifest["sha256"]}')


if __name__ == '__main__':
    main()
