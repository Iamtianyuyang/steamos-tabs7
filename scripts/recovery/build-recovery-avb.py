#!/usr/bin/env python3
"""Wrap the Linux recovery draft in the AVB format used by the working reference."""

import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[2]
    artifacts = root / 'out/bringup'
    tools = root / 'out/tools/avb'
    avb = [sys.executable, str(tools / 'avbtool.py')]
    key = tools / 'testkey_rsa4096.pem'
    subprocess.run([sys.executable, str(root / 'scripts/setup/prepare-avb-tools.py')], check=True)
    reference = root / 'out/recovery-reference/lineage/recovery.img'
    profile = json.loads((root / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    data = reference.read_bytes()
    if hashlib.sha256(data).hexdigest() != profile['files']['recovery']['sha256']:
        raise ValueError('Official recovery reference hash mismatch')
    if data[-64:-60] != b'AVBf':
        raise ValueError('Official recovery lacks the expected AVB footer')
    vm_offset = struct.unpack_from('>Q', data, len(data) - 44)[0]
    if data[vm_offset:vm_offset+4] != b'AVB0':
        raise ValueError('Invalid reference AVB metadata')
    auth_size = struct.unpack_from('>Q', data, vm_offset + 12)[0]
    algorithm = struct.unpack_from('>I', data, vm_offset + 28)[0]
    key_offset, key_size = struct.unpack_from('>QQ', data, vm_offset + 64)
    rollback_index = struct.unpack_from('>Q', data, vm_offset + 112)[0]
    flags, location = struct.unpack_from('>II', data, vm_offset + 120)
    if algorithm != 2 or flags != 0:
        raise ValueError('Unexpected reference AVB algorithm or flags')
    public = tools / 'testkey_rsa4096.avbpubkey'
    subprocess.run(avb + ['extract_public_key', '--key', str(key), '--output', str(public)], check=True)
    start = vm_offset + 256 + auth_size + key_offset
    if data[start:start+key_size] != public.read_bytes():
        raise ValueError('Published test key does not match the working recovery')
    subprocess.run(avb + ['verify_image', '--image', str(reference), '--key', str(key)], check=True)

    draft = artifacts / 'recovery-linux-draft.img'
    manifest = json.loads((artifacts / 'recovery-linux-manifest.json').read_text())
    if hashlib.sha256(draft.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Linux draft hash mismatch')
    # avbtool follows the hash descriptor's partition name when verifying;
    # keep recovery.img in an isolated directory for its self-readback.
    workspace = artifacts / 'avb-recovery'
    workspace.mkdir(parents=True, exist_ok=True)
    output = workspace / 'recovery.img'
    shutil.copyfile(draft, output)
    capacity = profile['device_pit']['partitions']['RECOVERY']['capacity_bytes']
    subprocess.run(avb + ['add_hash_footer', '--image', str(output), '--partition_size', str(capacity),
                         '--partition_name', 'recovery', '--algorithm', 'SHA256_RSA4096',
                         '--key', str(key), '--rollback_index', str(rollback_index),
                         '--rollback_index_location', str(location),
                         '--salt', hashlib.sha256(b'tabs7linux recovery development AVB').hexdigest(),
                         '--prop', 'com.android.build.recovery.fingerprint:samsung/tabs7linux_gts7lwifi/gts7lwifi:linux/6.18/development:userdebug/test-keys',
                         '--prop', 'com.tabs7linux.recovery.kind:mainline-linux-bringup'], check=True)
    subprocess.run(avb + ['verify_image', '--image', str(output), '--key', str(key)], check=True)
    signed = output.read_bytes()
    if len(signed) != capacity or signed[:draft.stat().st_size] != draft.read_bytes():
        raise ValueError('AVB wrapping changed the Linux payload or partition size')
    exported = artifacts / 'recovery-linux-avb.img'
    shutil.copyfile(output, exported)
    metadata = {
        'kind': 'Linux recovery with public development AVB signature',
        'source_draft_sha256': manifest['sha256'], 'image': exported.name,
        'header_source': manifest.get('header_source', 'factory'),
        'size': len(signed), 'sha256': hashlib.sha256(signed).hexdigest(),
        'algorithm': 'SHA256_RSA4096', 'rollback_index': rollback_index,
        'rollback_index_location': location, 'reference_public_key_matched': True,
        'native_boot_observed': False,
        'validation': 'cryptographic footer and exact payload checks; hardware test pending',
    }
    (artifacts / 'recovery-linux-avb-manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    info = subprocess.check_output(avb + ['info_image', '--image', str(output)], text=True)
    (artifacts / 'recovery-linux-avb-info.txt').write_text(info)
    print(f'AVB recovery verified: {exported}; public development key matches official recovery.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f'AVB packaging failed: {exc}')
