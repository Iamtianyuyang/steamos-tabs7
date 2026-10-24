#!/usr/bin/env python3
"""Stage the Android and Linux BOOT images for the recovery-side switcher.

Both images must be complete partition dumps: the bootloader only accepts a
whole-partition write, and the switcher refuses anything whose size or hash does
not match. This script verifies both images offline and writes the store that
tools/device/switch-boot.sh expects:

    <output>/android.img     stock Samsung BOOT (Android boots from it)
    <output>/linux.img       project BOOT (native Linux initramfs -> SteamOS)
    <output>/manifest.json   sizes and sha256 for both
    <output>/switch-boot.sh  copy of the recovery-side switcher

Push it to the device once a custom recovery is running:

    adb push <output> /data/media/0/tabs7linux/boot

Never writes to a device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json'
SWITCHER = ROOT / 'tools/device/switch-boot.sh'
AVB_KEY = ROOT / 'out/tools/avb/testkey_rsa4096.pem'
AVBTOOL = ROOT / 'out/tools/avb/avbtool.py'
LINUX_MAGIC = 0x644d5241


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def arm64_header(image_bytes):
    data = bytes(image_bytes[:0x40])
    return {'magic': struct.unpack_from('<I', data, 0x38)[0],
            'text_offset': struct.unpack_from('<Q', data, 8)[0],
            'image_size': struct.unpack_from('<Q', data, 0x10)[0]}


def kernel_bytes(boot_image, workdir):
    """Unpack a v2 BOOT and return its kernel payload."""
    unpack = ROOT / 'out/tools/mkbootimg/unpack_bootimg.py'
    if not unpack.is_file():
        raise ValueError(f'missing {unpack.relative_to(ROOT)} (scripts/setup/prepare-tools.sh)')
    destination = workdir / (Path(boot_image).stem + '-unpacked')
    subprocess.run([sys.executable, str(unpack), '--boot_img', str(boot_image),
                    '--out', str(destination), '--format=mkbootimg', '-0'],
                   check=True, capture_output=True)
    return (destination / 'kernel').read_bytes()


def verify_linux_boot(path, capacity, workdir):
    problems = []
    if path.stat().st_size != capacity:
        problems.append(f'{path.name} is {path.stat().st_size} bytes, partition is {capacity}')
    header = arm64_header(kernel_bytes(path, workdir))
    if header['magic'] != LINUX_MAGIC:
        problems.append(f'{path.name} kernel has no ARM64 magic')
    if AVBTOOL.is_file() and AVB_KEY.is_file():
        result = subprocess.run([sys.executable, str(AVBTOOL), 'verify_image',
                                 '--image', str(path), '--key', str(AVB_KEY)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            problems.append(f'{path.name} AVB signature does not verify with the project key')
    else:
        problems.append('avbtool/testkey missing, cannot verify the Linux BOOT signature')
    return header, problems


def verify_android_boot(path, capacity, workdir):
    problems = []
    if path.stat().st_size != capacity:
        problems.append(f'{path.name} is {path.stat().st_size} bytes, partition is {capacity}')
    header = arm64_header(kernel_bytes(path, workdir))
    if header['magic'] != LINUX_MAGIC:
        problems.append(f'{path.name} kernel has no ARM64 magic')
    # The stock image is signed with Samsung's key, which this project does not
    # have; only the AVB footer presence and the v2 header are checked.
    if b'AVBf' not in path.read_bytes()[-64:] and b'AVBf' not in path.read_bytes():
        problems.append(f'{path.name} has no AVB footer')
    return header, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--android-boot', type=Path, required=True,
                        help='stock Samsung BOOT image (full partition size)')
    parser.add_argument('--linux-boot', type=Path, required=True,
                        help='project BOOT image (full partition size)')
    parser.add_argument('--recovery', type=Path,
                        help='optional third-party recovery image (full RECOVERY partition size)')
    parser.add_argument('--output', type=Path, required=True, help='directory under out/')
    args = parser.parse_args()

    profile = json.loads(PROFILE.read_text())
    capacity = profile['device_pit']['partitions']['BOOT']['capacity_bytes']
    output = Path(args.output).resolve()
    if not output.is_relative_to((ROOT / 'out').resolve()):
        raise SystemExit('output must stay under out/')
    if output.exists():
        raise SystemExit(f'output already exists: {output}')
    output.mkdir(parents=True)
    workdir = output / 'unpacked'
    workdir.mkdir()

    android, linux = Path(args.android_boot).resolve(), Path(args.linux_boot).resolve()
    android_header, android_problems = verify_android_boot(android, capacity, workdir)
    linux_header, linux_problems = verify_linux_boot(linux, capacity, workdir)
    if android_problems or linux_problems:
        for problem in android_problems + linux_problems:
            print(f'ERROR: {problem}', file=sys.stderr)
        raise SystemExit('stage refused; fix the images first')

    recovery = Path(args.recovery).resolve() if args.recovery else None
    if recovery is not None:
        recovery_capacity = profile['device_pit']['partitions']['RECOVERY']['capacity_bytes']
        if not recovery.is_file():
            raise SystemExit(f'missing recovery image: {recovery}')
        if recovery.stat().st_size != recovery_capacity:
            raise SystemExit(f'{recovery.name} is {recovery.stat().st_size} bytes, '
                             f'RECOVERY partition is {recovery_capacity}')

    shutil.copyfile(android, output / 'android.img')
    shutil.copyfile(linux, output / 'linux.img')
    if recovery is not None:
        shutil.copyfile(recovery, output / 'recovery.img')
    shutil.copyfile(SWITCHER, output / 'switch-boot.sh')
    (output / 'switch-boot.sh').chmod(0o755)

    for name in ('android', 'linux') + (('recovery',) if recovery is not None else ()):
        (output / f'{name}.sha256').write_text(f'{sha(output / (name + ".img"))}  {name}.img\n')

    manifest = {
        'kind': 'tabs7linux dual-boot store',
        'boot_partition_capacity': capacity,
        'android': {'file': 'android.img', 'sha256': sha(output / 'android.img'),
                    'source': str(android), 'arm64_header': android_header,
                    'note': 'stock Samsung BOOT; signature belongs to Samsung'},
        'linux': {'file': 'linux.img', 'sha256': sha(output / 'linux.img'),
                  'source': str(linux), 'arm64_header': linux_header,
                  'note': 'project BOOT; AVB footer signed with the project test key'},
    }
    if recovery is not None:
        manifest['recovery'] = {
            'file': 'recovery.img', 'sha256': sha(output / 'recovery.img'),
            'source': str(recovery),
            'note': 'third-party recovery kept in place; restore it from the store if it is lost',
        }
    manifest.update({
        'usage': [
            'boot a custom recovery (TWRP) and get an adb or terminal shell',
            f'adb push {output} /data/media/0/tabs7linux/boot',
            'sh /data/media/0/tabs7linux/boot/switch-boot.sh status',
            'sh /data/media/0/tabs7linux/boot/switch-boot.sh android --reboot',
            'sh /data/media/0/tabs7linux/boot/switch-boot.sh linux --reboot',
            'restore the recovery itself: dd if=/data/media/0/tabs7linux/boot/recovery.img '
            'of=/dev/block/bootdevice/by-name/recovery bs=1M',
        ],
        'device_write_performed': False,
    })
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'android  : {manifest["android"]["sha256"]}')
    print(f'linux    : {manifest["linux"]["sha256"]}')
    print(f'store    : {output.relative_to(ROOT)}  (push it to /data/media/0/tabs7linux/boot)')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f'dual-boot staging failed: {error}')
