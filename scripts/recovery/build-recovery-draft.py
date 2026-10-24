#!/usr/bin/env python3
"""Package a Linux recovery candidate using factory or working Lineage headers."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--header-source', choices=['factory', 'lineage'], default='factory')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    artifacts = root / 'out/bringup'
    factory = root / 'out/stock-firmware/T870XXS8DXH1'
    tools = root / 'out/tools/mkbootimg'
    subprocess.run([sys.executable, str(root / 'scripts/build/mainline/verify-artifacts.py')], check=True)

    def cells(path, node, name):
        result = subprocess.check_output(['fdtget', '-t', 'x', str(path), node, name], text=True)
        return [int(value, 16) for value in result.split()]

    linux_dtb = artifacts / 'sm8250-samsung-gts7lwifi.dtb'
    reservations = []
    names = subprocess.check_output(['fdtget', '-l', str(linux_dtb), '/reserved-memory'], text=True).split()
    for name in names:
        reg = cells(linux_dtb, '/reserved-memory/' + name, 'reg')
        for index in range(0, len(reg), 4):
            address = (reg[index] << 32) | reg[index+1]
            size = (reg[index+2] << 32) | reg[index+3]
            reservations.append((address, address + size))
    expected = json.loads((factory / 'fixed-reservations.json').read_text())
    live_profile = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-live-recovery-rev7.json').read_text())
    live_expected = live_profile['fixed_reservations']
    for record in expected + live_expected:
        reg = record['reg']
        for index in range(0, len(reg), 4):
            address = (reg[index] << 32) | reg[index+1]
            size = (reg[index+2] << 32) | reg[index+3]
            if not any(start <= address and address + size <= end for start, end in reservations):
                raise SystemExit(f'Unprotected factory/live reservation: {record["name"]}')

    overlay = artifacts / 'bringup-noop.dtbo'
    subprocess.run(['dtc', '-I', 'dts', '-O', 'dtb', '-o', str(overlay),
                    str(root / 'hardware/samsung/gts7lwifi/devicetree/overlays/sm8250-samsung-gts7lwifi-bringup-overlay.dts')], check=True)
    applied = artifacts / 'noop-applied.dtb'
    subprocess.run(['fdtoverlay', '-i', str(linux_dtb), '-o', str(applied), str(overlay)], check=True)
    if cells(applied, '/', 'qcom,board-id') != [8, 7]:
        raise SystemExit('Recovery overlay changed the Linux board selector.')
    for name in names:
        if cells(applied, '/reserved-memory/' + name, 'reg') != cells(linux_dtb, '/reserved-memory/' + name, 'reg'):
            raise SystemExit(f'Recovery overlay changed a reservation: {name}')

    # Preserve the factory table's entry count and metadata. Samsung may select
    # by index as well as qcom,board-id. Keep its empty fallback blob in other
    # slots and place the matching no-op overlay in the revision-7 slot.
    original_table = (factory / 'recovery-unpacked/recovery_dtbo').read_bytes()
    magic, total, hsize, esize, count, start, pages, version = struct.unpack_from('>8I', original_table)
    if (magic, hsize, esize, start, version) != (0xd7b7ab1e, 32, 32, 32, 0):
        raise SystemExit('Unexpected recovery DTBO table format.')
    rows = [struct.unpack_from('>8I', original_table, start + index * esize) for index in range(count)]
    dummy = original_table[rows[0][1]:rows[0][1]+rows[0][0]]
    fallback = artifacts / 'factory-empty-overlay.dtbo'
    fallback.write_bytes(dummy)
    subprocess.run(['fdtoverlay', '-i', str(linux_dtb), '-o', str(applied), str(fallback)], check=True)
    selected = []
    for index in range(count):
        path = factory / f'dtbo-parts/overlay-{index}.dtbo'
        result = subprocess.run(['fdtget', '-t', 'x', str(path), '/', 'qcom,board-id'],
                                capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() == '8 7':
            selected.append(index)
    if len(selected) != 1:
        raise SystemExit('Expected exactly one recovery overlay for hardware revision 7.')
    blob = overlay.read_bytes()
    payloads = [blob if index == selected[0] else dummy for index in range(count)]
    cursor = 32 + 32 * count
    entries = []
    for row, payload in zip(rows, payloads):
        entries.append(struct.pack('>8I', len(payload), cursor, *row[2:]))
        cursor += len(payload)
    table_header = struct.pack('>8I', magic, cursor, hsize, esize, count, start, pages, version)
    table = artifacts / 'recovery-noop-dtbo.img'
    table.write_bytes(table_header + b''.join(entries) + b''.join(payloads))

    reference_dir = factory if args.header_source == 'factory' else root / 'out/recovery-reference/lineage'
    reference_image = reference_dir / 'recovery.img'
    unpack_dir = reference_dir / 'repack-reference'
    result = subprocess.run([sys.executable, str(tools / 'unpack_bootimg.py'),
                             '--boot_img', str(reference_image), '--out', str(unpack_dir),
                             '--format=mkbootimg', '-0'], check=True, capture_output=True)
    parts = result.stdout.split(b'\0')
    if parts[-1] == b'':
        parts.pop()
    original_args = [part.decode() for part in parts]
    if len(original_args) % 2:
        raise SystemExit('Unexpected AOSP repack arguments')
    options = dict(zip(original_args[::2], original_args[1::2]))
    if int(options.get('--header_version', '-1'), 0) != 2 or int(options.get('--pagesize', '-1'), 0) != 4096:
        raise SystemExit('Unexpected reference boot header')
    chosen = subprocess.check_output(['fdtget', '-t', 's', str(linux_dtb), '/chosen', 'bootargs'], text=True).strip()
    command_line = ((options.get('--cmdline', '') + ' ') if args.header_source == 'lineage' else '') + chosen
    options.update({'--kernel': str(artifacts / 'Image'),
                    '--ramdisk': str(artifacts / 'initramfs.cpio.gz'),
                    '--dtb': str(linux_dtb), '--recovery_dtbo': str(table),
                    '--cmdline': command_line + ' rdinit=/init printk.devkmsg=on'})
    candidate = artifacts / 'recovery-linux-draft.img'
    arguments = [part for pair in options.items() for part in pair]
    subprocess.run([sys.executable, str(tools / 'mkbootimg.py'), *arguments,
                    '--output', str(candidate)], check=True)
    if candidate.stat().st_size > (factory / 'recovery.img').stat().st_size:
        raise SystemExit('Candidate exceeds the factory recovery image size.')
    result_dir = artifacts / 'recovery-linux-unpacked'
    result = subprocess.run([sys.executable, str(tools / 'unpack_bootimg.py'),
                             '--boot_img', str(candidate), '--out', str(result_dir)],
                            check=True, capture_output=True, text=True)
    (artifacts / 'recovery-linux-info.txt').write_text(result.stdout)
    for component, source in [('kernel', artifacts / 'Image'), ('ramdisk', artifacts / 'initramfs.cpio.gz'),
                              ('dtb', linux_dtb), ('recovery_dtbo', table)]:
        if (result_dir / component).read_bytes() != source.read_bytes():
            raise SystemExit(f'Recovery repack mismatch: {component}')
    manifest = {'kind': 'unsigned Linux recovery draft',
                'header_source': args.header_source,
                'header_reference': str(reference_image),
                'factory_fixed_reservations_covered': len(expected),
                'live_recovery_fixed_reservations_covered': len(live_expected),
                'live_recovery_profile': 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-live-recovery-rev7.json',
                'recovery_dtbo_entry_count': count, 'recovery_dtbo_selected_index': selected[0],
                'image': candidate.name, 'size': candidate.stat().st_size,
                'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest(),
                'native_boot_tested': False,
                'linux_text_offset': struct.unpack_from('<Q', (artifacts / 'Image').read_bytes(), 8)[0],
                'factory_text_offset': struct.unpack_from('<Q', (factory / 'boot-unpacked/kernel').read_bytes(), 8)[0],
                'pre_flash_checks': ['verify device identity and complete partition snapshots',
                                     'verify official recovery and vbmeta device readbacks',
                                     'verify live FDT reservations and DTBO selection'],
                'remaining': ['native validation of ARM64 text_offset and recovery DTBO selection',
                              'native USB enumeration and initramfs execution']}
    (artifacts / 'recovery-linux-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Linux recovery draft round-trip verified: {candidate} ({candidate.stat().st_size} bytes)')
    print('Live and factory fixed reservations covered; verify device readbacks before a boot test.')


if __name__ == '__main__':
    main()
