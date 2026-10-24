#!/usr/bin/env python3
"""Read SM-T870 facts; optionally back up boot partitions from a root recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serial', help='ADB serial when multiple devices are connected')
    parser.add_argument('--backup', action='store_true', help='Read boot/dtbo/recovery/vbmeta; requires root ADB')
    parser.add_argument('--output', type=Path, help='Snapshot directory inside the project out/ directory')
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    destination = args.output.resolve() if args.output else project_root / 'out' / 'device'
    if not destination.is_relative_to(project_root / 'out'):
        raise SystemExit('Device snapshots must stay inside this project out/ directory.')
    destination.mkdir(parents=True, exist_ok=True)
    adb = ['adb'] + (['-s', args.serial] if args.serial else [])
    environment = dict(os.environ)
    # ADB 37's libusb backend did not discover this Samsung device here.
    environment.setdefault('ADB_LIBUSB', '0')

    def shell(*command):
        result = subprocess.run(adb + ['shell', *command], capture_output=True, timeout=20, env=environment)
        return {
            'ok': result.returncode == 0,
            'value': result.stdout.decode(errors='replace').strip(),
            'error': result.stderr.decode(errors='replace').strip(),
        }

    model = shell('getprop', 'ro.product.model')
    if not model['ok'] or model['value'] != 'SM-T870':
        raise SystemExit(f'Expected an authorized SM-T870: {model}')
    props = ['ro.product.model', 'ro.product.device', 'ro.boot.bootloader',
             'ro.boot.revision', 'ro.build.version.release',
             'ro.build.version.security_patch', 'ro.boot.flash.locked',
             'sys.oem_unlock_allowed', 'ro.oem_unlock_supported',
             'ro.boot.vbmeta.device_state', 'ro.boot.slot_suffix',
             'ro.bootmode', 'ro.twrp.version', 'ro.boot.verifiedbootstate', 'ro.boot.dtbo_idx',
             'ro.boot.veritymode', 'ro.boot.vbmeta.digest']
    report = {'properties': {prop: shell('getprop', prop) for prop in props}}
    report['uid'] = shell('id', '-u')
    report['kernel'] = shell('uname', '-a')
    report['cmdline'] = shell('cat', '/proc/cmdline')
    report['reserved_nodes'] = shell('ls', '/sys/firmware/devicetree/base/reserved-memory')
    report['pstore_nodes'] = shell('ls', '/sys/fs/pstore')

    def binary(remote, name, magic=None, expected_size=None):
        result = subprocess.run(adb + ['exec-out', 'cat', remote], capture_output=True, timeout=30, env=environment)
        valid = result.returncode == 0 and bool(result.stdout)
        if magic is not None:
            valid = valid and result.stdout.startswith(magic)
        if expected_size is not None:
            valid = valid and len(result.stdout) == expected_size
        if not valid:
            return {'ok': False, 'error': result.stderr.decode(errors='replace').strip()
                    or result.stdout[:160].decode(errors='replace').strip()}
        target = destination / name
        if target.exists() and target.read_bytes() != result.stdout:
            raise SystemExit(f'Preserving an existing different device snapshot: {target}')
        target.write_bytes(result.stdout)
        return {'ok': True, 'file': target.name, 'size': target.stat().st_size,
                'sha256': hashlib.sha256(result.stdout).hexdigest()}

    # A recovery exposes its own live FDT, which may differ from normal Android.
    report['fdt'] = binary('/sys/firmware/fdt', 'live.dtb', b'\xd0\x0d\xfe\xed')
    for prop in ['qcom,board-id', 'qcom,msm-id']:
        # These properties are eight bytes on the connected development tablet.
        # exec-out can return status 0 with a textual permission error in stdout.
        record = binary('/sys/firmware/devicetree/base/' + prop, prop + '.bin', expected_size=8)
        if record['ok']:
            data = (destination / record['file']).read_bytes()
            if len(data) % 4 == 0:
                record['cells'] = list(struct.unpack('>' + 'I' * (len(data) // 4), data))
        report[prop] = record

    report['backups'] = {}
    if args.backup:
        if report['uid']['value'] != '0':
            (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            raise SystemExit('Partition backup requires root ADB in recovery; no partitions were written.')
        profile = json.loads((project_root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
        for prop, expected in [('ro.boot.bootloader', profile['firmware']),
                               ('ro.boot.revision', profile['hardware_revision'])]:
            if report['properties'][prop]['value'] != expected:
                raise SystemExit(f'Backup layout does not match the connected device: {prop}')
        capacities = profile['device_pit']['partitions']
        for part, magic in [('boot', b'ANDROID!'), ('dtbo', b'\xd7\xb7\xab\x1e'),
                            ('recovery', b'ANDROID!'), ('vbmeta', b'AVB0')]:
            target = destination / (part + '-device.img')
            if target.exists():
                raise SystemExit(f'Preserving existing backup: {target}. Move it before collecting another.')
            temporary = destination / (part + '-device.img.partial')
            with temporary.open('wb') as stream:
                result = subprocess.run(adb + ['exec-out', 'cat', '/dev/block/by-name/' + part],
                                        stdout=stream, stderr=subprocess.PIPE, timeout=120, env=environment)
            with temporary.open('rb') as stream:
                header = stream.read(8)
            expected_size = capacities[part.upper()]['capacity_bytes']
            if result.returncode != 0 or not header.startswith(magic) or temporary.stat().st_size != expected_size:
                raise SystemExit(f'Invalid {part} backup; partial data retained at {temporary}')
            temporary.rename(target)
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            report['backups'][part] = {'file': target.name, 'size': target.stat().st_size,
                                      'sha256': digest}
            (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')

    (destination / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"SM-T870 revision {report['properties']['ro.boot.revision']['value']}, ADB uid {report['uid']['value']}")
    print(f"Live DTB readable: {report['fdt']['ok']}; report: {destination / 'report.json'}")


if __name__ == '__main__':
    main()
