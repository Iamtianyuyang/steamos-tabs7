#!/usr/bin/python3
"""Verify the pinned device firmware and set its native-kernel search path."""
import hashlib
import json
import os
from pathlib import Path


def prepare():
    if os.geteuid() != 0:
        raise RuntimeError('Firmware preparation requires root inside Ubuntu')
    manifest = Path('/etc/tabs7linux/wifi-firmware.json')
    if not manifest.is_file() or manifest.is_symlink():
        raise RuntimeError('Verified device firmware manifest is absent')
    files = json.loads(manifest.read_text())['files']
    if not 4 <= len(files) <= 64:
        raise RuntimeError('Unexpected firmware file count')
    native = Path('/proc/1/root/linux/lib/firmware')
    ubuntu = Path('/usr/lib/firmware')
    if native.stat().st_ino != ubuntu.stat().st_ino or native.stat().st_dev != ubuntu.stat().st_dev:
        raise RuntimeError('Native /linux does not refer to the Ubuntu firmware directory')
    required = {'qca6390/amss20.bin', 'qca6390/m3.bin', 'wlan/qca_cld/WCNSS_qcom_cfg.ini'}
    for record in files:
        relative = Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.parts[0] not in ('qca6390', 'wlan'):
            raise RuntimeError('Unexpected firmware path')
        file = ubuntu / relative
        if not file.is_file() or file.is_symlink():
            raise RuntimeError('Firmware file is not regular: ' + str(relative))
        if file.stat().st_size != record['size'] or hashlib.sha256(file.read_bytes()).hexdigest() != record['sha256']:
            raise RuntimeError('Firmware file does not match its manifest: ' + str(relative))
        required.discard(str(relative))
    if required:
        raise RuntimeError('Required firmware is missing')
    path = Path('/sys/module/firmware_class/parameters/path')
    path.write_text('/linux/lib/firmware')  # param_set_copystring retains newline!
    if path.read_bytes() != b'/linux/lib/firmware\n':
        raise RuntimeError('Kernel firmware path did not apply cleanly')
    return len(files)


if __name__ == '__main__':
    print('Firmware verified:', prepare(), 'files; native path configured without a stored newline.')
