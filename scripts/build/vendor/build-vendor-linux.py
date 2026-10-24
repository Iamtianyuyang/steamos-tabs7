#!/usr/bin/env python3
"""Build an offline native Linux bootstrap with the working 4.19 kernel.

No device access or flashing. Keep the official Lineage kernel, DTB, recovery
DTBO and header settings; replace Android recovery userspace with BusyBox.
"""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[3]
BUSYBOX_PACKAGE = 'busybox-static_1.36.1-6ubuntu3.1_arm64.deb'
BUSYBOX_PACKAGE_SHA256 = 'd96535e0402c011e0ee43449799df2f4504d44b842e4f2b3a6cbc845508eaafc'
BUSYBOX_SHA256 = '52151e7f322f926b64049cdaa1410dc3ea6485525e0624b05813791c219ae933'
REQUIRED_CONFIG = [
    'BLK_DEV_INITRD', 'RD_GZIP', 'TMPFS', 'PROC_FS', 'SYSFS', 'CONFIGFS_FS',
    'UNIX98_PTYS', 'INET', 'PSTORE', 'PSTORE_RAM', 'USB_GADGET', 'USB_CONFIGFS',
    'USB_CONFIGFS_RNDIS', 'USB_CONFIGFS_ACM', 'USB_DWC3', 'USB_DWC3_MSM',
]
APPLETS = 'sh mount mkdir echo sleep ls head ln ifconfig telnetd setsid dmesg cat ip reboot poweroff uname mdev mknod chmod stat readlink awk grep sed dd sync rm date'.split()
# Applets the helper calls as `busybox <applet>`; the first one has no symlink in
# the archive, so it is verified against the binary only.
PERSISTENT_GUI_APPLETS = ['chroot', 'umount', 'od', 'tr', 'id']
PERSISTENT_GUI_INLINE_APPLETS = ['mv']


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path, expected):
    data = path.read_bytes()
    if sha(data) != expected:
        raise ValueError(f'Input SHA-256 mismatch: {path}')
    return data


def newc_archive(busybox, init, persistent_gui=None):
    """Write deterministic newc, including console nodes without host root."""
    archive = bytearray()
    entries = [(name, stat.S_IFDIR | 0o755, b'', 0, 0)
               for name in ['.', 'bin', 'sbin', 'dev', 'proc', 'sys', 'run']]
    entries += [('bin/busybox', stat.S_IFREG | 0o755, busybox, 0, 0),
                ('init', stat.S_IFREG | 0o755, init, 0, 0)]
    if persistent_gui is not None:
        entries.append(('sbin/start-persistent-gui', stat.S_IFREG | 0o755,
                        persistent_gui, 0, 0))
    applets = APPLETS + (PERSISTENT_GUI_APPLETS if persistent_gui is not None else [])
    entries += [('bin/' + app, stat.S_IFLNK | 0o777, b'busybox', 0, 0)
                for app in applets]
    entries += [(name, stat.S_IFCHR | mode, b'', major, minor) for name, mode, major, minor in [
        ('dev/console', 0o600, 5, 1), ('dev/null', 0o666, 1, 3),
        ('dev/zero', 0o666, 1, 5), ('dev/tty', 0o666, 5, 0), ('dev/kmsg', 0o600, 1, 11),
    ]]
    entries.append(('TRAILER!!!', 0, b'', 0, 0))
    for inode, (name, mode, content, major, minor) in enumerate(entries, 1):
        filename = name.encode() + b'\0'
        fields = [inode, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1,
                  0, len(content), 0, 0, major, minor, len(filename), 0]
        archive += b'070701' + ''.join(f'{value:08x}' for value in fields).encode() + filename
        archive += b'\0' * (-len(archive) % 4)
        archive += content
        archive += b'\0' * (-len(archive) % 4)
    archive += b'\0' * (-len(archive) % 512)
    return bytes(archive)


def unpack(mktools, image, output):
    result = subprocess.run([sys.executable, str(mktools / 'unpack_bootimg.py'),
                             '--boot_img', str(image), '--out', str(output),
                             '--format=mkbootimg', '-0'], check=True, capture_output=True)
    parts = result.stdout.split(b'\0')
    if parts[-1] == b'':
        parts.pop()
    if len(parts) % 2:
        raise ValueError('Malformed AOSP boot repack arguments')
    values = [part.decode() for part in parts]
    return dict(zip(values[::2], values[1::2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-persistent-gui', action='store_true',
                        help='include the SteamOS hand-over helper; init always invokes it')
    parser.add_argument('--standard-drm-client', action='store_true',
                        help='disable downstream custom-client DRM behavior for standard compositors')
    args = parser.parse_args()
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    reference = ROOT / profile['files']['recovery']['path']
    original = checked_file(reference, profile['files']['recovery']['sha256'])
    capacity = profile['device_pit']['partitions']['RECOVERY']['capacity_bytes']
    if len(original) != capacity or original[-64:-60] != b'AVBf':
        raise ValueError('Unexpected official recovery capacity/footer')
    out = ROOT / 'out/vendor-linux'
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/setup/prepare-boot-tools.py')], check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/setup/prepare-avb-tools.py')], check=True)
    mktools = ROOT / 'out/tools/mkbootimg'
    options = unpack(mktools, reference, out / 'reference')
    if options.get('--header_version') != '2' or int(options.get('--pagesize', '0'), 0) != 4096:
        raise ValueError('Unexpected reference boot image header')

    # Extract configuration from the verified kernel itself, not a mutable cache.
    kernel = (out / 'reference/kernel').read_bytes()
    start = kernel.index(b'IKCFG_ST') + 8
    finish = kernel.index(b'IKCFG_ED', start)
    config_text = gzip.decompress(kernel[start:finish]).decode()
    (out / 'kernel.config').write_text(config_text)
    config = dict(line.split('=', 1) for line in config_text.splitlines()
                  if line.startswith('CONFIG_') and '=' in line)
    required_config = REQUIRED_CONFIG + (['F2FS_FS'] if args.with_persistent_gui else [])
    for name in required_config:
        if config.get('CONFIG_' + name) != 'y':
            raise ValueError(f'Required built-in vendor kernel capability absent: {name}')
    if config.get('CONFIG_DEVTMPFS') == 'y':
        raise ValueError('Reference changed: review the tmpfs /dev design')
    if struct.unpack_from('<H', kernel, 0)[0] == 0x5a4d:
        raise ValueError('Unexpected reference EFI image')
    if struct.unpack_from('<Q', kernel, 8)[0] != 0x80000:
        raise ValueError('Unexpected working vendor kernel text_offset')

    package = ROOT / 'out/tools/debs' / BUSYBOX_PACKAGE
    checked_file(package, BUSYBOX_PACKAGE_SHA256)
    arch = subprocess.check_output(['dpkg-deb', '-f', str(package), 'Architecture'], text=True).strip()
    if arch != 'arm64':
        raise ValueError('BusyBox must target arm64')
    payload = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(package)])
    with tarfile.open(fileobj=io.BytesIO(payload), mode='r:') as package_tar:
        busybox = package_tar.extractfile('./usr/bin/busybox').read()
    if sha(busybox) != BUSYBOX_SHA256 or busybox[:6] != b'\x7fELF\x02\x01':
        raise ValueError('BusyBox must match the known ARM64 ELF')
    if struct.unpack_from('<H', busybox, 18)[0] != 183:
        raise ValueError('BusyBox ELF is not AArch64')
    phoff = struct.unpack_from('<Q', busybox, 32)[0]
    phsize, phcount = struct.unpack_from('<HH', busybox, 54)
    if any(struct.unpack_from('<I', busybox, phoff + i * phsize)[0] == 3 for i in range(phcount)):
        raise ValueError('BusyBox unexpectedly needs a dynamic interpreter')
    applets = APPLETS + (PERSISTENT_GUI_APPLETS if args.with_persistent_gui else [])
    inline = PERSISTENT_GUI_INLINE_APPLETS if args.with_persistent_gui else []
    for applet in applets + inline:
        if b'\0' + applet.encode() + b'\0' not in busybox:
            raise ValueError(f'Expected BusyBox applet name absent: {applet}')
    init = (ROOT / 'boot/initramfs/init-vendor-linux').read_bytes()
    subprocess.run(['sh', '-n', str(ROOT / 'boot/initramfs/init-vendor-linux')], check=True)
    persistent_gui = None
    if args.with_persistent_gui:
        helper = ROOT / 'boot/initramfs/start-persistent-gui'
        subprocess.run(['sh', '-n', str(helper)], check=True)
        persistent_gui = helper.read_bytes()
    archive = newc_archive(busybox, init, persistent_gui)
    listing = subprocess.check_output(['cpio', '--list', '--quiet'], input=archive).decode().splitlines()
    if not all(name in listing for name in ['init', 'bin/busybox', 'bin/mdev', 'bin/mknod', 'dev/console']):
        raise ValueError('Incomplete vendor Linux initramfs archive')
    if args.with_persistent_gui and 'sbin/start-persistent-gui' not in listing:
        raise ValueError('Persistent GUI helper missing from initramfs archive')
    ramdisk = out / 'initramfs.cpio.gz'
    ramdisk.write_bytes(gzip.compress(archive, compresslevel=9, mtime=0))

    # Kernel, DTB, DTBO and hardware-related command line stay from the reference.
    original_options = dict(options)
    options['--ramdisk'] = str(ramdisk)
    extra_cmdline = 'rdinit=/init printk.devkmsg=on'
    if args.standard_drm_client:
        extra_cmdline += ' msm_drm.sdecustom=0'
    options['--cmdline'] += ' ' + extra_cmdline
    draft = out / 'recovery-vendor-linux-draft.img'
    arguments = [part for pair in options.items() for part in pair]
    subprocess.run([sys.executable, str(mktools / 'mkbootimg.py'), *arguments,
                    '--output', str(draft)], check=True)
    draft_options = unpack(mktools, draft, out / 'unpacked')
    path_options = {'--kernel', '--ramdisk', '--dtb', '--recovery_dtbo'}
    for name, value in original_options.items():
        if name not in path_options | {'--cmdline'} and draft_options.get(name) != value:
            raise ValueError(f'Boot header setting changed: {name}')
    components = {}
    for name in ['kernel', 'dtb', 'recovery_dtbo']:
        data = (out / 'reference' / name).read_bytes()
        if (out / 'unpacked' / name).read_bytes() != data:
            raise ValueError(f'Working reference component changed: {name}')
        components[name] = {'size': len(data), 'sha256': sha(data), 'unchanged': True}
    if (out / 'unpacked/ramdisk').read_bytes() != ramdisk.read_bytes():
        raise ValueError('BusyBox ramdisk changed during repack')

    tools = ROOT / 'out/tools/avb'
    avb = [sys.executable, str(tools / 'avbtool.py')]
    key = tools / 'testkey_rsa4096.pem'
    vm_offset = struct.unpack_from('>Q', original, len(original) - 44)[0]
    if original[vm_offset:vm_offset + 4] != b'AVB0':
        raise ValueError('Invalid official recovery AVB metadata')
    auth_size = struct.unpack_from('>Q', original, vm_offset + 12)[0]
    algorithm = struct.unpack_from('>I', original, vm_offset + 28)[0]
    key_offset, key_size = struct.unpack_from('>QQ', original, vm_offset + 64)
    rollback = struct.unpack_from('>Q', original, vm_offset + 112)[0]
    flags, location = struct.unpack_from('>II', original, vm_offset + 120)
    if algorithm != 2 or flags != 0:
        raise ValueError('Unexpected reference AVB algorithm/flags')
    public = out / 'reference.avbpubkey'
    subprocess.run(avb + ['extract_public_key', '--key', str(key), '--output', str(public)], check=True)
    key_start = vm_offset + 256 + auth_size + key_offset
    if original[key_start:key_start + key_size] != public.read_bytes():
        raise ValueError('Public development key does not match the working reference')
    subprocess.run(avb + ['verify_image', '--image', str(reference), '--key', str(key)], check=True)
    signed = out / 'recovery.img'
    signed.write_bytes(draft.read_bytes())
    subprocess.run(avb + ['add_hash_footer', '--image', str(signed), '--partition_size', str(capacity),
                         '--partition_name', 'recovery', '--algorithm', 'SHA256_RSA4096',
                         '--key', str(key), '--rollback_index', str(rollback),
                         '--rollback_index_location', str(location),
                         '--salt', sha(b'tabs7linux vendor-kernel Linux control'),
                         '--prop', 'com.android.build.recovery.fingerprint:samsung/tabs7linux_gts7lwifi/gts7lwifi:linux/4.19/development:userdebug/test-keys',
                         '--prop', 'com.tabs7linux.recovery.kind:native-vendor-linux-control'], check=True)
    subprocess.run(avb + ['verify_image', '--image', str(signed), '--key', str(key)], check=True)
    final = signed.read_bytes()
    draft_data = draft.read_bytes()
    if len(final) != capacity or final[:len(draft_data)] != draft_data:
        raise ValueError('Signed image size or payload mismatch')
    info = subprocess.check_output(avb + ['info_image', '--image', str(signed)], text=True)
    (out / 'avb-info.txt').write_text(info)
    manifest = {
        'kind': ('Native Linux BusyBox bootstrap with persistent Ubuntu desktop'
                 if args.with_persistent_gui else 'Native Linux BusyBox RAM userspace with working Lineage vendor kernel'),
        'kernel_series': '4.19', 'mainline_kernel': False, 'native_boot_observed': False,
        'image': 'recovery.img', 'size': len(final), 'sha256': sha(final),
        'reference_recovery_sha256': sha(original), 'components': components,
        'reference_header_settings_preserved': True,
        'command_line_added': extra_cmdline,
        'standard_drm_client': args.standard_drm_client,
        'initramfs_sha256': sha(ramdisk.read_bytes()), 'init_sha256': sha(init),
        'busybox_package_sha256': BUSYBOX_PACKAGE_SHA256,
        'busybox_sha256': BUSYBOX_SHA256, 'busybox_elf_static': True,
        'algorithm': 'SHA256_RSA4096', 'rollback_index': rollback,
        'rollback_index_location': location, 'reference_public_key_matched': True,
        'required_builtin_kernel_capabilities': required_config,
        'persistent_gui_helper_included': args.with_persistent_gui,
        'persistent_gui_helper_sha256': sha(persistent_gui) if persistent_gui is not None else None,
        'usb': {'functions': ['RNDIS', 'ACM'], 'tablet': '192.168.77.2/24',
                'host': '192.168.77.1/24', 'vendor_product': '1d6b:0104'},
        'storage': ('RAM bootstrap; the helper validates userdata and hands the root to the SteamOS tree tabs7linux/steamos'
                    if args.with_persistent_gui else 'RAM-only; Android system/vendor/userdata are not mounted'),
        'pstore': 'mounted early; existing files preserved',
        'validation': 'offline header/component/static ELF/cpio/AVB signature and hash checks',
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Offline vendor-kernel Linux control verified: {signed}; SHA-256 {sha(final)}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f'Vendor Linux build failed: {exc}')
