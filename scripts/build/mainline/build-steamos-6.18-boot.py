#!/usr/bin/env python3
"""Pack the mainline 6.18 kernel into a BOOT image candidate (never flashed here).

Reuses the header and command line of the deployed Linux BOOT and swaps the
three payload components: the 6.18 ``Image``, the 6.18 DTB and the already
deployed initramfs. The AVB footer is recreated with the same rollback index
and location, so the result has the same shape as the working image.

Before writing anything it verifies, in this order:

* the same-boot partition snapshot (manifest hashes, PIT capacity, DTBO and
  RECOVERY layout, live DTB, board-id/msm-id);
* the deployed BOOT signature, header v2 and command line;
* that the kernel really is 6.18 and that the DTB contains the panel node;
* after packing, that only kernel/dtb/ramdisk changed and the image still fits
  the BOOT partition exactly.

Everything lands under ``out/``. Flashing is a separate, authorised step.
"""
import argparse
import hashlib
import importlib.util
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARTITIONS = ('boot', 'dtbo', 'recovery', 'vbmeta')
# New salt: the footer hash has no reason to repeat the ramdisk-only update.
AVB_SALT = 'tabs7linux steamos 6.18 boot candidate'
COMPONENTS = ('--kernel', '--ramdisk', '--dtb')


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def file_sha(path):
    return sha(Path(path).read_bytes())


def display(path):
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def normalise_arm64_header(image, reference):
    """Copy the informational arm64 header fields the Samsung LK insists on.

    Mainline sets text_offset to 0 and leaves res3 empty; the vendor 4.19 image
    the bootloader accepted carries text_offset=0x80000 and res3=0xecefecef.
    Both fields are ignored by the kernel since 5.9, but LK refuses to boot an
    image without them, so mirror the reference values.
    """
    data = bytearray(image)
    text_offset = struct.unpack_from('<Q', reference, 8)[0]
    res3 = reference[0x28:0x30]
    struct.pack_into('<Q', data, 8, text_offset)
    data[0x28:0x30] = res3
    if struct.unpack_from('<I', data, 0x38)[0] != 0x644d5241:
        raise ValueError('arm64 magic damaged while normalising the header')
    return bytes(data), {'text_offset': text_offset,
                         'res3': struct.unpack('<Q', res3)[0]}


def kernel_version(image_bytes):
    marker = image_bytes.find(b'Linux version ')
    if marker < 0:
        raise ValueError('kernel Image has no version string')
    return image_bytes[marker:image_bytes.find(b'\0', marker)].decode(errors='replace')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--snapshot', type=Path, required=True,
                        help='same-boot snapshot with boot/dtbo/recovery/vbmeta')
    parser.add_argument('--deployed-boot', type=Path, required=True,
                        help='BOOT image on the device now (header and command line template)')
    parser.add_argument('--kernel', type=Path, required=True, help='6.18 Image')
    parser.add_argument('--dtb', type=Path, required=True, help='6.18 gts7lwifi DTB')
    parser.add_argument('--ramdisk', type=Path, required=True, help='deployed initramfs.cpio.gz')
    parser.add_argument('--output', type=Path, required=True, help='directory under out/')
    parser.add_argument('--append-cmdline', default='',
                        help='arguments appended to the deployed command line (debug boots)')
    parser.add_argument('--experiment', action='store_true',
                        help='isolate a component: skip the 6.18 kernel and panel-node guards')
    args = parser.parse_args()

    preflight = load('boot_preflight', 'scripts/build/vendor/preflight-steamos-boot.py')
    builder = load('vendor_builder', 'scripts/build/vendor/build-vendor-linux.py')
    update = load('update_linux_boot', 'scripts/build/vendor/update-linux-boot.py')
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/profiles/'
                          'gts7lwifi-recovery-bootstrap.json').read_text())

    snapshot = Path(args.snapshot).resolve()
    manifest = preflight.snapshot_manifest(snapshot)
    checked = preflight.verify_snapshot(snapshot, manifest, profile, update.LAYOUT)
    print(f'snapshot : {display(snapshot)} (layout, live DTB and identity verified)')

    tools = Path(ROOT / 'out/tools')
    avb = [sys.executable, str(tools / 'avb/avbtool.py')]
    key = tools / 'avb/testkey_rsa4096.pem'
    mktools = tools / 'mkbootimg'

    deployed = Path(args.deployed_boot).resolve()
    subprocess.run(avb + ['verify_image', '--image', str(deployed), '--key', str(key)],
                   check=True, capture_output=True)
    output = preflight.ensure_under_out(args.output)
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    output.mkdir(parents=True)

    options = builder.unpack(mktools, deployed, output / 'template')
    if options.get('--header_version') != '2' or '--recovery_dtbo' in options:
        raise ValueError('deployed BOOT is not the reviewed header v2 Linux image')
    deployed_cmdline = options.get('--cmdline', '')
    if args.append_cmdline:
        options['--cmdline'] = f'{deployed_cmdline} {args.append_cmdline.strip()}'.strip()
    # Everything except the payloads must survive the repack; the command line
    # is compared against the deployed one plus the explicit --append-cmdline.
    original_options = dict(options)
    print(f'deployed : {display(deployed)} ({file_sha(deployed)})')
    print(f'template : header v2, command line {len(deployed_cmdline)} bytes')
    if args.append_cmdline:
        print(f'cmdline  : appended {args.append_cmdline.strip()!r} '
              f'({len(options["--cmdline"])} bytes)')

    kernel = Path(args.kernel).resolve()
    dtb = Path(args.dtb).resolve()
    ramdisk = Path(args.ramdisk).resolve()
    reference_kernel = (output / 'template' / 'kernel').read_bytes()
    kernel_data, header_fields = normalise_arm64_header(kernel.read_bytes(), reference_kernel)
    if args.experiment:
        # the vendor kernel already carries the Samsung header fields
        kernel_data = kernel.read_bytes()
    version = kernel_version(kernel_data)
    if not args.experiment:
        if not version.split()[2].startswith('6.18.'):
            raise ValueError(f'kernel is not 6.18: {version}')
        if b'samsung,gts7l-nt36523' not in dtb.read_bytes():
            raise ValueError('DTB does not contain the NT36523 panel node')
    staged = {'kernel': output / kernel.name, 'dtb': output / dtb.name,
              'ramdisk': output / ramdisk.name}
    staged['kernel'].write_bytes(kernel_data)
    (output / dtb.name).write_bytes(dtb.read_bytes())
    (output / ramdisk.name).write_bytes(ramdisk.read_bytes())
    print(f'kernel   : {version}')
    print(f'header   : text_offset=0x{header_fields["text_offset"]:x} '
          f'res3=0x{header_fields["res3"]:x} (copied from the deployed kernel)')
    print(f'dtb      : {display(dtb)} (panel node present)')

    options['--kernel'] = str(output / kernel.name)
    options['--dtb'] = str(output / dtb.name)
    options['--ramdisk'] = str(output / ramdisk.name)
    draft = output / 'boot-payload.img'
    subprocess.run([sys.executable, str(mktools / 'mkbootimg.py'),
                    *(part for pair in options.items() for part in pair),
                    '--output', str(draft)], check=True)

    rebuilt = builder.unpack(mktools, draft, output / 'rebuilt')
    for name, value in original_options.items():
        if name in COMPONENTS:
            continue
        if rebuilt.get(name) != value:
            raise ValueError(f'boot header/command line changed: {name}')
    expected_components = {'kernel': staged['kernel'].read_bytes(),
                           'dtb': dtb.read_bytes(), 'ramdisk': ramdisk.read_bytes()}
    for name, expected in expected_components.items():
        if (output / 'rebuilt' / name).read_bytes() != expected:
            raise ValueError(f'repacked component changed: {name}')

    capacity = profile['device_pit']['partitions']['BOOT']['capacity_bytes']
    if draft.stat().st_size > capacity:
        raise ValueError(f'payload {draft.stat().st_size} exceeds BOOT capacity {capacity}')
    original = checked['paths']['boot'].read_bytes()
    vm_offset = struct.unpack_from('>Q', original, len(original) - 44)[0]
    rollback = struct.unpack_from('>Q', original, vm_offset + 112)[0]
    location = struct.unpack_from('>I', original, vm_offset + 124)[0]
    signed = output / 'boot.img'
    signed.write_bytes(draft.read_bytes())
    subprocess.run(avb + ['add_hash_footer', '--image', str(signed),
                          '--partition_size', str(capacity), '--partition_name', 'boot',
                          '--algorithm', 'SHA256_RSA4096', '--key', str(key),
                          '--rollback_index', str(rollback),
                          '--rollback_index_location', str(location),
                          '--salt', sha(AVB_SALT),
                          '--prop', 'com.tabs7linux.boot.kind:mainline-6.18-steamos-candidate'],
                   check=True, capture_output=True)
    subprocess.run(avb + ['verify_image', '--image', str(signed), '--key', str(key)],
                   check=True, capture_output=True)
    if signed.stat().st_size != capacity:
        raise ValueError(f'signed image is {signed.stat().st_size}, expected {capacity}')

    components = {name: {'path': display(path), 'bytes': path.stat().st_size,
                         'sha256': file_sha(path)} for name, path in staged.items()}
    report = {
        'kind': ('component isolation experiment' if args.experiment
                 else 'mainline 6.18 SteamOS BOOT candidate'),
        'image': display(signed), 'image_sha256': file_sha(signed),
        'image_bytes': signed.stat().st_size, 'partition_capacity': capacity,
        'template_boot': {'path': display(deployed), 'sha256': file_sha(deployed)},
        'snapshot': {'path': display(snapshot), 'boot_id': checked['boot_id'],
                     'partitions': {name: file_sha(checked['paths'][name]) for name in PARTITIONS}},
        'components': components,
        'kernel_version': version,
        'arm64_header': header_fields,
        'header_and_commandline_preserved': not args.append_cmdline,
        'appended_cmdline': args.append_cmdline.strip(),
        'hardware_boot_observed': False,
        'device_write_performed': False,
        'flash_requires_user_authorization': True,
        'notes': [
            'mainline 6.18 has never booted on this device',
            'the initramfs is the deployed 4.19 one; it needs partlabel or PARTNAME for userdata',
            'flash only with an authorised samloader write plus a readback comparison',
        ],
    }
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'boot.img : {display(signed)} {report["image_sha256"]}')
    print(f'capacity : {capacity} bytes exactly; AVB footer verified')
    print('nothing was written to the device')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f'6.18 BOOT candidate failed: {error}')
