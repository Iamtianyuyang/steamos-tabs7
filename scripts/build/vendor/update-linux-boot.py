#!/usr/bin/env python3
"""Offline ramdisk-only update of the working SM-T870 Linux BOOT image.

Layout after the SteamOS migration: BOOT holds Linux, DTBO the official
Lineage table, RECOVERY TWRP. Requires a same-boot partition snapshot and an
initramfs review from build-steamos-initramfs.py. Kernel, DTB and the whole
boot header must stay unchanged. Never accesses a device.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
LAYOUT = {
    'dtbo': '9383ef37364107e8de37ac0c1b12fe2b502cfc2d53cf7b68bc780ec831bb81c9',  # Lineage 20261011 dtbo.img
    'recovery': '68214dc9cedf5b2fe4c5b01800fb0411552c8a401c052ee093ad0bb8c64a5e46',  # TWRP 3.7.1_12-0
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked(path, expected):
    data = path.read_bytes()
    if digest(data) != expected:
        raise ValueError(f'SHA-256 mismatch: {path}')
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True, help='same-boot snapshot with boot/dtbo/recovery/vbmeta')
    parser.add_argument('--ramdisk', type=Path, required=True)
    parser.add_argument('--ramdisk-review', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    backup = args.backup.resolve()
    evidence = json.loads((backup / 'manifest.json').read_text())
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    images = {}
    for name in ('boot', 'dtbo', 'recovery', 'vbmeta'):
        images[name] = checked(backup / (name + '.img'), evidence['partitions'][name]['sha256'])
        if len(images[name]) != profile['device_pit']['partitions'][name.upper()]['capacity_bytes']:
            raise ValueError(f'Incomplete partition backup: {name}')
    for name, expected in LAYOUT.items():
        if digest(images[name]) != expected:
            raise ValueError(f'{name.upper()} is not in the expected SteamOS migration layout')
    if images['vbmeta'][:4] != b'AVB0' or struct.unpack_from('>I', images['vbmeta'], 120)[0] != 3:
        raise ValueError('vbmeta verification is not disabled as recorded')

    review_bytes = args.ramdisk_review.read_bytes()
    review = json.loads(review_bytes)
    ramdisk = checked(args.ramdisk, review['ramdisk_sha256'])

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location('vendor_builder', ROOT / 'scripts/build/vendor/build-vendor-linux.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    mktools = ROOT / 'out/tools/mkbootimg'
    avb = [sys.executable, str(ROOT / 'out/tools/avb/avbtool.py')]
    key = ROOT / 'out/tools/avb/testkey_rsa4096.pem'
    subprocess.run(avb + ['verify_image', '--image', str(backup / 'boot.img'), '--key', str(key)], check=True)
    original = images['boot']
    options = builder.unpack(mktools, backup / 'boot.img', out / 'original')
    if options.get('--header_version') != '2' or '--recovery_dtbo' in options:
        raise ValueError('Working BOOT is not the reviewed header v2 Linux image')
    if digest((out / 'original/ramdisk').read_bytes()) != review['reference_sha256']:
        raise ValueError('Initramfs review was made against a different ramdisk')
    original_options = dict(options)
    (out / 'initramfs.cpio.gz').write_bytes(ramdisk)
    options['--ramdisk'] = str(out / 'initramfs.cpio.gz')
    draft = out / 'boot-payload.img'
    subprocess.run([sys.executable, str(mktools / 'mkbootimg.py'),
                    *(part for pair in options.items() for part in pair), '--output', str(draft)], check=True)
    rebuilt = builder.unpack(mktools, draft, out / 'rebuilt')
    for name, value in original_options.items():
        if name not in {'--kernel', '--ramdisk', '--dtb'} and rebuilt.get(name) != value:
            raise ValueError(f'Boot header/command line changed: {name}')
    components = {}
    for name in ('kernel', 'ramdisk', 'dtb'):
        data = (out / 'rebuilt' / name).read_bytes()
        expected = ramdisk if name == 'ramdisk' else (out / 'original' / name).read_bytes()
        if data != expected:
            raise ValueError(f'Repacked component changed: {name}')
        components[name] = {'bytes': len(data), 'sha256': digest(data), 'unchanged': name != 'ramdisk'}

    vm_offset = struct.unpack_from('>Q', original, len(original) - 44)[0]
    rollback = struct.unpack_from('>Q', original, vm_offset + 112)[0]
    location = struct.unpack_from('>I', original, vm_offset + 124)[0]
    signed = out / 'boot.img'
    signed.write_bytes(draft.read_bytes())
    subprocess.run(avb + ['add_hash_footer', '--image', str(signed), '--partition_size', str(len(original)),
                         '--partition_name', 'boot', '--algorithm', 'SHA256_RSA4096', '--key', str(key),
                         '--rollback_index', str(rollback), '--rollback_index_location', str(location),
                         # Pinned: reproduces the deployed BOOT image; do not reword.
                         '--salt', digest(b'tabs7linux steamos boot-target initramfs'),
                         '--prop', 'com.tabs7linux.boot.kind:native-vendor-linux-steamos-selector'], check=True)
    subprocess.run(avb + ['verify_image', '--image', str(signed), '--key', str(key)], check=True)
    final = signed.read_bytes()
    payload = draft.read_bytes()
    if len(final) != len(original) or final[:len(payload)] != payload:
        raise ValueError('Final BOOT capacity/payload changed')
    manifest = {'kind': 'ramdisk-only BOOT update', 'image_sha256': digest(final), 'image_bytes': len(final),
                'rollback_image': str(backup / 'boot.img'), 'rollback_sha256': digest(original),
                'snapshot_boot_id': evidence['boot_id'], 'ramdisk_review_sha256': digest(review_bytes),
                'changed_entries': review['changed_entries'], 'components': components,
                'header_and_commandline_preserved': True, 'layout_checked': LAYOUT,
                'native_boot_observed': False, 'device_write_performed': False}
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Verified ramdisk-only BOOT: {signed}; SHA-256 {digest(final)}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f'BOOT update failed: {error}')
