#!/usr/bin/env python3
"""Re-sign the device vbmeta with the dtbo digest of a rebuilt DTBO.

The bootloader and Odin accept a DTBO only when its hash matches the dtbo hash
descriptor in vbmeta (measured: a rebuilt table, unsigned or with a footer
signed by the vbmeta key, stops at "Custom Binary(boot) Blocked By OEM Lock"
and Odin answers "FAIL! Auth"; the Lineage table, whose hash vbmeta records,
boots). vbmeta itself is signed with the AOSP test key, so it can be rebuilt:
every descriptor, property, flag, rollback index and the key stay as they are,
only the dtbo hash descriptor is replaced by the one in the new DTBO's footer.

    scripts/build/mainline/build-vbmeta.py \\
        --source out/device/preflash-6.18-20261024-172138/vbmeta.img \\
        --dtbo out/dual-boot/dtbo-6.18.img \\
        --output out/dual-boot/vbmeta-6.18.img

Switching back to Lineage/4.19 means writing the original vbmeta again
together with the Lineage DTBO.
"""
import argparse
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
AVB = ROOT / 'out/tools/avb'


def load_avbtool():
    spec = importlib.util.spec_from_file_location('avbtool', AVB / 'avbtool.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse(avbtool, path):
    return avbtool.Avb()._parse_image(avbtool.ImageHandler(str(path), read_only=True))


def dtbo_descriptor(avbtool, descriptors):
    found = [d for d in descriptors
             if isinstance(d, avbtool.AvbHashDescriptor) and d.partition_name == 'dtbo']
    if len(found) != 1:
        raise SystemExit(f'expected one dtbo hash descriptor, found {len(found)}')
    return found[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source', type=Path, required=True, help='current vbmeta partition dump')
    parser.add_argument('--dtbo', type=Path, required=True, help='DTBO image with an AVB hash footer')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    avbtool = load_avbtool()
    avb = avbtool.Avb()
    source = args.source.read_bytes()
    _, header, descriptors, _ = parse(avbtool, args.source)
    old = dtbo_descriptor(avbtool, descriptors)
    _, _, dtbo_descriptors, _ = parse(avbtool, args.dtbo)
    new = dtbo_descriptor(avbtool, dtbo_descriptors)

    # The descriptor must describe the bytes that will be on the partition.
    dtbo = args.dtbo.read_bytes()
    digest = hashlib.sha256(new.salt + dtbo[:new.image_size]).digest()
    if digest != new.digest:
        raise SystemExit('the DTBO footer digest does not match its contents')

    key = AVB / 'testkey_rsa4096.pem'
    with tempfile.TemporaryDirectory() as temp:
        extracted = Path(temp) / 'key.bin'
        subprocess.run([sys.executable, str(AVB / 'avbtool.py'), 'extract_public_key',
                        '--key', str(key), '--output', str(extracted)], check=True)
        public_key = extracted.read_bytes()
    start = header.SIZE + header.authentication_data_block_size + header.public_key_offset
    if source[start:start + header.public_key_size] != public_key:
        raise SystemExit('the source vbmeta is not signed with the AOSP test key; refusing')

    replaced = [new if d is old else d for d in descriptors]
    blob = avb._generate_vbmeta_blob(
        'SHA256_RSA4096', str(key), None, replaced, None, None,
        header.rollback_index, header.flags, header.rollback_index_location,
        None, None, None, False, None, None, None, None,
        header.release_string, None, header.required_libavb_version_minor)
    if len(blob) > len(source):
        raise SystemExit(f'vbmeta blob {len(blob)} exceeds the partition {len(source)}')
    blob += b'\0' * (len(source) - len(blob))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(blob)
    _, check, check_descriptors, _ = parse(avbtool, args.output)
    assert (check.rollback_index, check.flags, check.rollback_index_location) == \
        (header.rollback_index, header.flags, header.rollback_index_location)
    assert len(check_descriptors) == len(descriptors)
    assert dtbo_descriptor(avbtool, check_descriptors).digest == new.digest
    print(f'source   : {args.source} rollback {header.rollback_index} flags {header.flags} '
          f'{len(descriptors)} descriptors')
    print(f'dtbo     : {old.digest.hex()[:16]}… -> {new.digest.hex()[:16]}… '
          f'({new.image_size} bytes)')
    print(f'output   : {args.output} {len(blob)} bytes sha256 {hashlib.sha256(blob).hexdigest()}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f'vbmeta rebuild failed: {error}')
