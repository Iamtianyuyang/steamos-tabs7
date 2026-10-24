#!/usr/bin/env python3
"""Build a no-op DTBO partition image so mainline DTB boots are not rejected.

The bootloader applies the DTBO entry selected for this hardware (dtbo_idx=6) to
the DTB inside BOOT. With the Lineage table that overlay references vendor nodes
(`&soc`, `&mdss`, ...) and fails with FDT_ERR_NOTFOUND on a mainline tree, after
which the bootloader drops to Download mode (measured: vendor kernel + mainline
DTB fails identically).

This script keeps the table metadata of the working image - count, entry size,
page size and the id/rev columns the bootloader reads - and replaces every entry
payload with the factory empty overlay (entry 0 of the working table). Both the
vendor and the mainline DTB accept an empty overlay, so the same DTBO works for
either BOOT; Android still needs the real table, so the dual-boot switcher writes
them as a pair.

    scripts/build/mainline/build-noop-dtbo.py \\
        --source out/steamos/native-preflash3/dtbo.img \\
        --vendor-dtb out/steamos/boot-preflight-*/boot/original/dtb \\
        --mainline-dtb out/steamos-kernel-6.2/sm8250-samsung-gts7lwifi.dtb \\
        --output out/dual-boot/dtbo-linux.img
"""
import argparse
import hashlib
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

MAGIC = 0xD7B7AB1E
HEADER = struct.Struct('>8I')
ENTRY = struct.Struct('>8I')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse(image):
    magic, total, header_size, entry_size, count, entries_offset, page_size, version = \
        HEADER.unpack_from(image, 0)
    if magic != MAGIC:
        raise ValueError(f'not a DTBO table: magic 0x{magic:x}')
    if header_size != HEADER.size or entry_size != ENTRY.size:
        raise ValueError('unexpected DTBO header/entry size')
    entries = [ENTRY.unpack_from(image, entries_offset + index * entry_size) for index in range(count)]
    payloads = [image[entry[1]:entry[1] + entry[0]] for entry in entries]
    return {'total': total, 'page_size': page_size, 'version': version,
            'magic': magic, 'count': count, 'entries': entries, 'payloads': payloads}


def applies_cleanly(overlay, dtb, workdir):
    overlay_path = workdir / 'overlay.dtbo'
    overlay_path.write_bytes(overlay)
    result = subprocess.run(['fdtoverlay', '-i', str(dtb), '-o', str(workdir / 'merged.dtb'),
                             str(overlay_path)], capture_output=True, text=True)
    return result.returncode, result.stderr.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True, help='working DTBO partition dump')
    parser.add_argument('--vendor-dtb', type=Path, required=True)
    parser.add_argument('--mainline-dtb', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    image = args.source.read_bytes()
    table = parse(image)
    empty = table['payloads'][0]
    print(f'source   : {args.source.name} {len(image)} bytes, {table["count"]} entries, '
          f'empty overlay {len(empty)} bytes')

    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        for dtb in (args.vendor_dtb, args.mainline_dtb):
            code, error = applies_cleanly(empty, dtb, workdir)
            if code != 0:
                raise SystemExit(f'factory empty overlay does not apply to {dtb.name}: {error}')
        print('check    : the empty overlay applies to both the vendor and the mainline DTB')

    header_size = HEADER.size
    entry_size = ENTRY.size
    entries_offset = header_size
    payloads_offset = entries_offset + table['count'] * entry_size
    payloads_offset += -payloads_offset % table['page_size']

    entries, cursor = [], payloads_offset
    for entry in table['entries']:
        entries.append(ENTRY.pack(len(empty), cursor, *entry[2:]))
        cursor += len(empty)
    total = cursor
    blob = bytearray()
    blob += HEADER.pack(MAGIC, total, header_size, entry_size, table['count'], entries_offset,
                        table['page_size'], table['version'])
    blob += b''.join(entries)
    blob += b'\0' * (payloads_offset - len(blob))
    for entry in table['entries']:
        blob += empty
    if len(blob) != total:
        raise SystemExit(f'assembled {len(blob)} bytes but the header claims {total}')
    if len(blob) > len(image):
        raise SystemExit(f'no-op table ({len(blob)}) does not fit the source size ({len(image)})')
    blob += b'\0' * (len(image) - len(blob))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(blob))
    check = parse(bytes(blob))
    assert check['count'] == table['count'] and check['total'] == total
    print(f'output   : {out} {len(blob)} bytes sha256 {sha(bytes(blob))}')
    print(f'entries  : {check["count"]} x {len(empty)} byte empty overlay')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, struct.error, subprocess.SubprocessError) as error:
        sys.exit(f'no-op DTBO build failed: {error}')
