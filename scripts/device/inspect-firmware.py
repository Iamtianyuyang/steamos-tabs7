#!/usr/bin/env python3
"""Extract original boot components and reconstruct the revision-7 factory DTB."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ap', type=Path, required=True, help='T870XXS8DXH1 AP tar.md5 archive')
    args = parser.parse_args()
    if 'T870XXS8DXH1' not in args.ap.name:
        raise SystemExit('This port currently targets firmware T870XXS8DXH1.')
    root = Path(__file__).resolve().parents[2]
    destination = root / 'out/stock-firmware/T870XXS8DXH1'
    destination.mkdir(parents=True, exist_ok=True)

    def preserve(path, data):
        if path.exists() and path.read_bytes() != data:
            raise SystemExit(f'Preserving a different existing firmware component: {path}')
        path.write_bytes(data)

    manifest = {'source_archive': str(args.ap.resolve()),
                'kind': 'firmware package originals; not device partition dumps', 'files': {}}
    with tarfile.open(args.ap, 'r:') as archive:
        for name in ['boot.img.lz4', 'recovery.img.lz4', 'dtbo.img.lz4', 'vbmeta.img.lz4']:
            member = archive.getmember(name)
            if not member.isfile() or member.size > 128 * 1024 * 1024:
                raise SystemExit(f'Unexpected boot component: {name}')
            preserve(destination / name, archive.extractfile(member).read())
            raw = destination / name.removesuffix('.lz4')
            temporary = raw.with_suffix('.img.new')
            subprocess.run(['lz4', '-d', '-f', str(destination / name), str(temporary)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            preserve(raw, temporary.read_bytes())
            temporary.unlink()
            with raw.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            manifest['files'][raw.name] = {'size': raw.stat().st_size, 'sha256': digest}
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    profile = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-T870XXS8DXH1-rev7.json').read_text())
    if manifest['files'] != profile['original_images']:
        raise SystemExit('Firmware components differ from the recorded port profile; inspect before using them.')

    unpacker = root / 'out/tools/mkbootimg/unpack_bootimg.py'
    if not unpacker.exists():
        raise SystemExit('Run scripts/setup/prepare-boot-tools.py first.')
    for partition in ['boot', 'recovery']:
        result = subprocess.run([sys.executable, str(unpacker), '--boot_img', str(destination / (partition + '.img')),
                                 '--out', str(destination / (partition + '-unpacked'))],
                                check=True, capture_output=True, text=True)
        (destination / (partition + '-info.txt')).write_text(result.stdout)

    def property(path, name, kind='x', node='/'):
        result = subprocess.run(['fdtget', '-t', kind, str(path), node, name], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    blob = (destination / 'boot-unpacked/dtb').read_bytes()
    base_dir = destination / 'dtb-parts'
    base_dir.mkdir(exist_ok=True)
    offset = 0
    bases = []
    while offset < len(blob):
        if len(blob) - offset < 40:
            raise SystemExit('Truncated concatenated factory DTB')
        magic, size = struct.unpack_from('>II', blob, offset)
        if magic != 0xd00dfeed or not 40 <= size <= len(blob) - offset:
            raise SystemExit('Invalid concatenated factory DTB')
        path = base_dir / f'base-{offset:x}.dtb'
        preserve(path, blob[offset:offset+size])
        if property(path, 'qcom,msm-id') == '164 20001':
            bases.append(path)
        offset += size

    raw = (destination / 'dtbo.img').read_bytes()
    magic, total, hsize, esize, count, start, pages, version = struct.unpack_from('>8I', raw)
    if magic != 0xd7b7ab1e or total > len(raw) or hsize < 32 or esize < 32 or start + count * esize > total:
        raise SystemExit('Invalid factory DTBO table')
    overlay_dir = destination / 'dtbo-parts'
    overlay_dir.mkdir(exist_ok=True)
    overlays = []
    for index in range(count):
        size, offset, ident, rev, *custom = struct.unpack_from('>8I', raw, start + index * esize)
        if offset + size > total or raw[offset:offset+4] != b'\xd0\x0d\xfe\xed':
            raise SystemExit('Invalid factory DTBO entry')
        path = overlay_dir / f'overlay-{index}.dtbo'
        preserve(path, raw[offset:offset+size])
        if property(path, 'qcom,board-id') == '8 7':
            overlays.append(path)
    if len(bases) != 1 or len(overlays) != 1:
        raise SystemExit(f'Expected one v2.1 base and one revision-7 overlay: {bases}, {overlays}')
    for path, field in [(bases[0], 'base_dtb_sha256'), (overlays[0], 'overlay_sha256')]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != profile[field]:
            raise SystemExit(f'Factory DT selection differs from the recorded profile: {path.name}')
    reconstructed = destination / 'reconstructed-stock-rev7.dtb'
    subprocess.run(['fdtoverlay', '-i', str(bases[0]), '-o', str(reconstructed), str(overlays[0])], check=True)

    records = []
    names = subprocess.check_output(['fdtget', '-l', str(reconstructed), '/reserved-memory'], text=True).split()
    for name in names:
        value = property(reconstructed, 'reg', node='/reserved-memory/' + name)
        if value is not None:
            records.append({'name': name, 'reg': [int(cell, 16) for cell in value.split()]})
    (destination / 'fixed-reservations.json').write_text(json.dumps(records, indent=2) + '\n')
    if {item['name']: item['reg'] for item in records} != {item['name']: item['reg'] for item in profile['fixed_reservations']}:
        raise SystemExit('Reconstructed factory reservations differ from the recorded profile.')
    print(f'Original firmware components extracted; reconstructed {len(records)} fixed reservations.')
    print(f'Reconstructed factory DTB: {reconstructed}')


if __name__ == '__main__':
    main()
