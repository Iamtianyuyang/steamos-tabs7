#!/usr/bin/env python3
"""Check the built ARM64 image, DTB, configuration, and initramfs contracts."""
import gzip
import hashlib
import json
from pathlib import Path
import struct
import subprocess


def main():
    root = Path(__file__).resolve().parents[3]
    artifacts = root / 'out' / 'bringup'
    image = (artifacts / 'Image').read_bytes()
    assert image[56:60] == b'ARM\x64', 'Missing ARM64 Image magic'
    offset, image_size, flags = struct.unpack_from('<QQQ', image, 8)
    assert image_size > 0 and flags & 1 == 0, 'Expected a little-endian ARM64 image'
    assert (flags >> 1) & 3 == 1, 'Expected 4 KiB pages'
    assert gzip.decompress((artifacts / 'Image.gz').read_bytes()) == image

    requested = [line for line in (root / 'hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config').read_text().splitlines()
                 if line.startswith('CONFIG_')]
    actual = set((artifacts / 'kernel.config').read_text().splitlines())
    assert all(line in actual for line in requested), 'Incomplete built kernel configuration'

    dtb = artifacts / 'sm8250-samsung-gts7lwifi.dtb'
    data = dtb.read_bytes()
    assert data[:4] == b'\xd0\x0d\xfe\xed'
    assert struct.unpack_from('>I', data, 4)[0] == len(data) <= 2 * 1024 * 1024

    def cells(node, prop):
        result = subprocess.check_output(['fdtget', '-t', 'x', str(dtb), node, prop], text=True)
        return [int(value, 16) for value in result.split()]

    # base DTB stays 0 0 like the vendor one; the DTBO entry supplies 8 7
    assert cells('/', 'qcom,board-id') == [0, 0]
    assert cells('/', 'qcom,msm-id') == [356, 0x20001]
    assert cells('/reserved-memory', '#address-cells') == [2]
    assert cells('/reserved-memory', '#size-cells') == [2]
    assert cells('/reserved-memory/ramoops@9fa00000', 'reg') == [0, 0x9fa00000, 0, 0x100000]
    reservations = []
    names = subprocess.check_output(['fdtget', '-l', str(dtb), '/reserved-memory'], text=True).split()
    for name in names:
        reg = cells('/reserved-memory/' + name, 'reg')
        assert len(reg) % 4 == 0
        for index in range(0, len(reg), 4):
            address = (reg[index] << 32) | reg[index + 1]
            size = (reg[index + 2] << 32) | reg[index + 3]
            assert size > 0
            reservations.append((address, address + size, name))
    reservations.sort()
    for previous, following in zip(reservations, reservations[1:]):
        assert previous[1] <= following[0], f'Overlapping reservations: {previous[2]}, {following[2]}'

    archive = gzip.decompress((artifacts / 'initramfs.cpio.gz').read_bytes())
    position = 0
    entries = {}
    while True:
        assert archive[position:position + 6] == b'070701', 'Expected newc initramfs'
        fields = [int(archive[position + 6 + i * 8:position + 14 + i * 8], 16) for i in range(13)]
        mode, uid, gid, size, name_size = fields[1], fields[2], fields[3], fields[6], fields[11]
        name = archive[position + 110:position + 110 + name_size - 1].decode()
        body = (position + 110 + name_size + 3) & ~3
        if name == 'TRAILER!!!':
            break
        assert uid == gid == 0, f'Unexpected ownership: {name}'
        entries[name.removeprefix('./')] = (mode, archive[body:body + size])
        position = (body + size + 3) & ~3
    assert entries['init'][0] & 0o111
    assert entries['init'][1] == (root / 'boot/initramfs/init').read_bytes()
    busybox = entries['bin/busybox'][1]
    assert busybox[:6] == b'\x7fELF\x02\x01'
    assert struct.unpack_from('<H', busybox, 18)[0] == 183, 'Expected AArch64 BusyBox'
    phoff = struct.unpack_from('<Q', busybox, 32)[0]
    phsize, phcount = struct.unpack_from('<HH', busybox, 54)
    assert all(struct.unpack_from('<I', busybox, phoff + i * phsize)[0] != 3
               for i in range(phcount)), 'BusyBox must be statically linked'
    for applet in ['sh', 'mount', 'mkdir', 'echo', 'sleep', 'ls', 'head', 'ln',
                   'ifconfig', 'telnetd', 'setsid', 'dmesg', 'cat', 'ip', 'reboot', 'poweroff']:
        assert entries['bin/' + applet][1] == b'busybox'

    manifest = {
        'validation': 'offline artifact checks; native hardware boot not yet tested',
        'text_offset': offset, 'image_memory_size': image_size, 'image_flags': flags,
        'requested_config_count': len(requested), 'reserved_region_count': len(reservations),
        'source_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        'source_tree_dirty': bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True)),
        'files': {},
    }
    for name in ['Image', 'Image.gz', dtb.name, 'initramfs.cpio.gz', 'kernel.config']:
        path = artifacts / name
        manifest['files'][name] = {'size': path.stat().st_size,
                                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    for relative in ['hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config', 'boot/initramfs/init',
                     'linux-sm8250-v6.18/arch/arm64/boot/dts/qcom/sm8250-samsung-gts7lwifi.dts',
                     'linux-sm8250-v6.18/arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi']:
        manifest['files'][relative] = {'sha256': hashlib.sha256((root / relative).read_bytes()).hexdigest()}
    (artifacts / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'ARM64 Image/gzip, {len(requested)} config settings, DTB, {len(reservations)} non-overlapping reservations, and static initramfs verified.')
    print('This confirms artifact structure, not native boot on the tablet.')


if __name__ == '__main__':
    main()
