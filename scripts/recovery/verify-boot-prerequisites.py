#!/usr/bin/env python3
"""Check captured device evidence before a Linux recovery test. Never flash."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, default=root / 'out/device/lineage-recovery')
    args = parser.parse_args()
    snapshot = args.snapshot.resolve()
    report = json.loads((snapshot / 'report.json').read_text())
    factory = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-T870XXS8DXH1-rev7.json').read_text())
    bootstrap = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    live = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-live-recovery-rev7.json').read_text())
    if report['uid']['value'] != '0':
        raise ValueError('Snapshot was not collected with root ADB')
    for key, expected in [('ro.product.model', 'SM-T870'), ('ro.boot.bootloader', factory['firmware']),
                          ('ro.boot.revision', '7'), ('ro.boot.flash.locked', '0')]:
        if report['properties'][key]['value'] != expected:
            raise ValueError(f'Device snapshot identity mismatch: {key}')
    if report['qcom,board-id']['cells'] != live['board_id'] or report['qcom,msm-id']['cells'] != live['msm_id']:
        raise ValueError('Live hardware selectors mismatch')
    if int(report['properties']['ro.boot.dtbo_idx']['value']) != live['dtbo_index']:
        raise ValueError('Unexpected live DTBO selection')

    verified = {}
    for name in ('boot', 'dtbo', 'recovery', 'vbmeta'):
        path = snapshot / (name + '-device.img')
        size = bootstrap['device_pit']['partitions'][name.upper()]['capacity_bytes']
        if path.stat().st_size != size:
            raise ValueError(f'{name}: incomplete device partition snapshot')
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        expected = (factory['original_images'][name + '.img']['sha256'] if name in ('boot', 'dtbo')
                    else bootstrap['files']['recovery']['sha256'] if name == 'recovery'
                    else report['backups']['vbmeta']['sha256'])
        if digest != expected:
            raise ValueError(f'{name}: partition snapshot hash mismatch')
        verified[name] = {'size': size, 'sha256': digest}
    vbmeta = (snapshot / 'vbmeta-device.img').read_bytes()
    if struct.unpack_from('>I', vbmeta, 120)[0] != 3:
        raise ValueError('AVB disabling flags missing')
    prefix = vbmeta[:bootstrap['files']['vbmeta']['size']]
    if hashlib.sha256(prefix).hexdigest() != bootstrap['files']['vbmeta']['sha256']:
        raise ValueError('Device vbmeta does not match the official input')
    if hashlib.sha256((snapshot / 'live.dtb').read_bytes()).hexdigest() != live['live_fdt_sha256']:
        raise ValueError('Live FDT snapshot hash mismatch')

    linux = root / 'out/bringup/sm8250-samsung-gts7lwifi.dtb'
    names = subprocess.check_output(['fdtget', '-l', str(linux), '/reserved-memory'], text=True).split()
    ranges = []
    for name in names:
        text = subprocess.check_output(['fdtget', '-t', 'x', str(linux), '/reserved-memory/' + name, 'reg'], text=True)
        cells = [int(value, 16) for value in text.split()]
        for i in range(0, len(cells), 4):
            address = (cells[i] << 32) | cells[i+1]
            size = (cells[i+2] << 32) | cells[i+3]
            ranges.append((address, address + size))
    for entry in live['fixed_reservations'] + factory['fixed_reservations']:
        cells = entry['reg']
        for i in range(0, len(cells), 4):
            address = (cells[i] << 32) | cells[i+1]
            size = (cells[i+2] << 32) | cells[i+3]
            if not any(start <= address and address + size <= end for start, end in ranges):
                raise ValueError(f'Unprotected reservation: {entry["name"]}')
    evidence = {'validation': 'captured recovery device readbacks; not native Linux boot',
                'snapshot': str(snapshot.relative_to(root)), 'partitions': verified,
                'vbmeta_flags': 3, 'live_fixed_ranges_covered': len(live['fixed_reservations']),
                'factory_fixed_ranges_covered': len(factory['fixed_reservations']),
                'dtbo_index': live['dtbo_index'], 'live_fdt_sha256': live['live_fdt_sha256']}
    (root / 'out/bringup/preflash-checks.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print('Device snapshots, official AVB/recovery readbacks, DTBO index, and live/factory reservations verified.')
    print('Refresh live device identity before writing; hardware boot remains untested.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        sys.exit(f'Boot prerequisites failed: {exc}')
