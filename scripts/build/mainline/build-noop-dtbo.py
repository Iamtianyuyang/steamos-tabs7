#!/usr/bin/env python3
"""Build a no-op DTBO partition image so mainline DTB boots are not rejected.

The bootloader applies the DTBO entry selected for this hardware (dtbo_idx=6) to
the DTB inside BOOT. With the Lineage table that overlay references vendor nodes
(`&soc`, `&mdss`, ...) and fails with FDT_ERR_NOTFOUND on a mainline tree, after
which the bootloader drops to Download mode (measured: vendor kernel + mainline
DTB fails identically).

This script keeps the table metadata of the working image - count, entry size,
page size and the id/rev columns - and replaces every entry payload with the
factory empty overlay (entry 0 of the working table), except the entry selected
for this hardware. The table's id/rev columns are all zero; the bootloader picks
the entry whose own root qcom,board-id matches (8 7 for revision 7). A table of
only empty overlays has no such entry, and BOOT then dropped to Download mode
even with the vendor kernel and DTB (measured), so the selected entry gets
--board-overlay: an empty fragment whose root carries the board-id, like the
recovery DTBO that let a mainline DTB past the bootloader. Android still needs
the real table, so BOOT and DTBO are written as a pair.

    scripts/build/mainline/build-noop-dtbo.py \\
        --source out/steamos/native-preflash3/dtbo.img \\
        --board-overlay hardware/samsung/gts7lwifi/devicetree/overlays/sm8250-samsung-gts7lwifi-boot-dtbo-overlay.dts \\
        --vendor-dtb out/steamos/boot-preflight-*/boot/original/dtb \\
        --mainline-dtb out/steamos-kernel-6.18/sm8250-samsung-gts7lwifi.dtb \\
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
    parser.add_argument('--board-overlay', type=Path, required=True,
                        help='overlay source (.dts) for the entry selected by --board-id')
    parser.add_argument('--board-id', default='8 7', help='qcom,board-id of the selected entry')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    image = args.source.read_bytes()
    table = parse(image)
    empty = table['payloads'][0]
    print(f'source   : {args.source.name} {len(image)} bytes, {table["count"]} entries, '
          f'empty overlay {len(empty)} bytes')

    with tempfile.TemporaryDirectory() as temp:
        workdir = Path(temp)
        selected = []
        for index, payload in enumerate(table['payloads']):
            path = workdir / f'entry-{index}.dtbo'
            path.write_bytes(payload)
            found = subprocess.run(['fdtget', '-t', 'x', str(path), '/', 'qcom,board-id'],
                                   capture_output=True, text=True).stdout.split()
            if [int(cell, 16) for cell in found] == [int(cell) for cell in args.board_id.split()]:
                selected.append(index)
        if len(selected) != 1:
            raise SystemExit(f'expected exactly one source entry with board-id {args.board_id}, '
                             f'found {selected}; is --source the real (Lineage) table?')
        compiled = workdir / 'board.dtbo'
        subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(compiled),
                        str(args.board_overlay)], check=True)
        board = compiled.read_bytes()
        found = subprocess.run(['fdtget', '-t', 'x', str(compiled), '/', 'qcom,board-id'],
                               capture_output=True, text=True).stdout.split()
        if [int(cell, 16) for cell in found] != [int(cell) for cell in args.board_id.split()]:
            raise SystemExit(f'{args.board_overlay.name} does not carry board-id {args.board_id}')
        for dtb in (args.vendor_dtb, args.mainline_dtb):
            for name, overlay in (('factory empty', empty), ('board', board)):
                code, error = applies_cleanly(overlay, dtb, workdir)
                if code != 0:
                    raise SystemExit(f'{name} overlay does not apply to {dtb.name}: {error}')
        print(f'check    : the empty and board overlays apply to both the vendor and the mainline DTB')
        print(f'selected : entry {selected[0]} gets {args.board_overlay.name} '
              f'(board-id {args.board_id}, {len(board)} bytes)')
    payloads = [board if index == selected[0] else empty for index in range(table['count'])]

    header_size = HEADER.size
    entry_size = ENTRY.size
    entries_offset = header_size
    payloads_offset = entries_offset + table['count'] * entry_size
    payloads_offset += -payloads_offset % table['page_size']

    entries, cursor = [], payloads_offset
    for entry, payload in zip(table['entries'], payloads):
        entries.append(ENTRY.pack(len(payload), cursor, *entry[2:]))
        cursor += len(payload)
    total = cursor
    blob = bytearray()
    blob += HEADER.pack(MAGIC, total, header_size, entry_size, table['count'], entries_offset,
                        table['page_size'], table['version'])
    blob += b''.join(entries)
    blob += b'\0' * (payloads_offset - len(blob))
    for payload in payloads:
        blob += payload
    if len(blob) != total:
        raise SystemExit(f'assembled {len(blob)} bytes but the header claims {total}')
    if len(blob) > len(image):
        raise SystemExit(f'no-op table ({len(blob)}) does not fit the source size ({len(image)})')
    # The bootloader (and Odin) accept a partition only if its AVB footer is
    # signed with the vbmeta key, or if its hash equals the digest vbmeta
    # records. Lineage's DTBO uses the second way; a rebuilt table cannot, so
    # sign its footer with the same AOSP test key as the BOOT images. Without
    # this the boot stops at "Custom Binary(boot) Blocked By OEM Lock" and Odin
    # reports "FAIL! Auth" (both measured).
    blob += b'\0' * (-len(blob) % 4096)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(bytes(blob))
    avb = Path(__file__).resolve().parents[3] / 'out/tools/avb'
    subprocess.run([sys.executable, str(avb / 'avbtool.py'), 'add_hash_footer', '--image', str(out),
                    '--partition_size', str(len(image)), '--partition_name', 'dtbo',
                    '--algorithm', 'SHA256_RSA4096', '--key', str(avb / 'testkey_rsa4096.pem'),
                    '--salt', sha(b'tabs7linux mainline dtbo')], check=True)
    with tempfile.TemporaryDirectory() as temp:
        # avbtool looks for the descriptor's data as <partition>.img next to the image
        named = Path(temp) / 'dtbo.img'
        named.write_bytes(out.read_bytes())
        subprocess.run([sys.executable, str(avb / 'avbtool.py'), 'verify_image', '--image', str(named),
                        '--key', str(avb / 'testkey_rsa4096.pem')], check=True, capture_output=True)
    final = out.read_bytes()
    if len(final) != len(image):
        raise SystemExit(f'footered image is {len(final)} bytes, expected {len(image)}')
    check = parse(final)
    assert check['count'] == table['count'] and check['total'] == total
    print(f'output   : {out} {len(final)} bytes sha256 {sha(final)} (AVB footer, {len(blob)} byte table)')
    print(f'entries  : {check["count"]}, sizes {[entry[0] for entry in check["entries"]]}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, struct.error, subprocess.SubprocessError) as error:
        sys.exit(f'no-op DTBO build failed: {error}')
