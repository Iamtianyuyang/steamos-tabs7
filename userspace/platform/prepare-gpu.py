#!/usr/bin/python3
"""Verify SM-T870 GPU firmware before the compositor first opens KGSL."""
import hashlib
import json
import os
from pathlib import Path


def prepare():
    if os.geteuid() != 0:
        raise RuntimeError('Run GPU preparation as root inside native Ubuntu')
    if Path('/sys/class/kgsl/kgsl-3d0/gpu_model').read_text().strip() != 'Adreno650v3':
        raise RuntimeError('This profile requires the verified SM-T870 Adreno 650')
    manifest = Path('/etc/tabs7linux/gpu-firmware.json')
    if not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError('GPU firmware manifest is missing')
    firmware = json.loads(manifest.read_text())['firmware']
    expected = {'a650_sqe.fw', 'a650_gmu.bin', 'a650_zap.mdt',
                'a650_zap.b00', 'a650_zap.b01', 'a650_zap.b02'}
    if len(firmware) != len(expected) or {r['path'] for r in firmware} != expected:
        raise RuntimeError('Unexpected Adreno firmware set')
    root = Path('/usr/lib/firmware')
    native = Path('/proc/1/root/linux/lib/firmware')
    if (root.stat().st_ino, root.stat().st_dev) != (native.stat().st_ino, native.stat().st_dev):
        raise RuntimeError('Native kernel and Ubuntu firmware directories differ')
    for record in firmware:
        file = root / record['path']
        if not file.is_file() or file.is_symlink() or file.stat().st_size != record['size']:
            raise RuntimeError('GPU firmware is not the verified regular file: ' + file.name)
        if hashlib.sha256(file.read_bytes()).hexdigest() != record['sha256']:
            raise RuntimeError('GPU firmware checksum mismatch: ' + file.name)
    parameter = Path('/sys/module/firmware_class/parameters/path')
    parameter.write_text('/linux/lib/firmware')
    if parameter.read_bytes() != b'/linux/lib/firmware\n':
        raise RuntimeError('GPU firmware path did not apply cleanly')
    print('Adreno 650 firmware verified; native search path configured.')


if __name__ == '__main__':
    prepare()
