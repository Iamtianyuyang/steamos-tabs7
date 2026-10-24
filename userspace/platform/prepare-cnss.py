#!/usr/bin/python3
"""Verify the private CNSS runtime and bind kernel views; never start a daemon."""
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess

RUNTIME = Path('/usr/local/lib/tabs7linux/cnss-android')
MANIFEST = Path('/etc/tabs7linux/cnss-runtime.json')
RUN = Path('/run/network')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def protected(info):
    require(info.st_uid == 0 and not info.st_mode & 0o022,
            'CNSS runtime inputs must be root-owned and not writable by other users')


def directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode), 'Expected an actual directory: ' + str(path))
    protected(info)


def relative_path(value):
    require(isinstance(value, str) and bool(value), 'Empty/invalid manifest path')
    path = PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and str(path) == value
            and path.parts[0] in ('vendor', 'system', 'apex')
            and re.fullmatch(r'[A-Za-z0-9_./+@-]+', value), 'Unsafe manifest path')
    return path


def checked_parents(root, relative):
    path = root
    for part in relative.parts[:-1]:
        path /= part
        directory(path)
    return root / relative


def verify_runtime(root, manifest):
    require(root.is_absolute() and root.resolve() == root,
            'CNSS runtime path must not traverse symlinks')
    directory(root)
    info = manifest.lstat()
    require(stat.S_ISREG(info.st_mode), 'CNSS manifest must be a regular file')
    protected(info)
    spec = json.loads(manifest.read_text())
    require(spec.get('closure_complete') is True and spec.get('elf_count') == 24,
            'CNSS manifest must describe the complete verified 24-file closure')
    records = spec.get('files')
    require(isinstance(records, list) and len(records) == 24, 'Unexpected CNSS file count')
    seen = set()
    for record in records:
        relative = relative_path(record['path'])
        require(str(relative) not in seen, 'Duplicate CNSS manifest file')
        seen.add(str(relative))
        path = checked_parents(root, relative)
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode), 'CNSS ELF must not be a symlink/device: ' + str(relative))
        protected(info)
        require(info.st_size == record['size'], 'CNSS file size mismatch: ' + str(relative))
        data = path.read_bytes()
        require(len(data) >= 64 and data[:6] == b'\x7fELF\x02\x01'
                and struct.unpack_from('<H', data, 18)[0] == 183,
                'CNSS file is not a little-endian AArch64 ELF: ' + str(relative))
        require(hashlib.sha256(data).hexdigest() == record['sha256'],
                'CNSS file SHA-256 mismatch: ' + str(relative))
    require({'vendor/bin/cnss-daemon', 'apex/com.android.runtime/bin/linker64'} <= seen,
            'CNSS daemon/linker is absent')
    for name in ('vendor/bin/cnss-daemon', 'apex/com.android.runtime/bin/linker64'):
        require((root / name).stat().st_mode & 0o111, 'CNSS executable permission is absent')
    links = spec.get('symlinks')
    require(isinstance(links, dict) and len(links) == 7, 'Unexpected CNSS aliases')
    for value, target in links.items():
        relative = relative_path(value)
        path = checked_parents(root, relative)
        require(path.is_symlink() and os.readlink(path) == target,
                'CNSS alias mismatch: ' + value)
        require(path.resolve().is_relative_to(root) and path.resolve().exists(),
                'CNSS alias escapes its runtime: ' + value)
        # Linux symlink mode is always 0777; the protected parent controls edits.
        require(path.lstat().st_uid == 0, 'CNSS alias must be root-owned: ' + value)
    return spec


def mount_points():
    points = set()
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        fields = line.split()
        require(len(fields) >= 7, 'Invalid mountinfo record')
        point = re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), fields[4])
        points.add(Path(point))
    return points


def same_directory(left, right):
    first, second = left.stat(), right.stat()
    return (first.st_dev, first.st_ino) == (second.st_dev, second.st_ino)


def bind_directory(source, destination):
    directory(source)
    directory(destination)
    if same_directory(source, destination):
        return
    require(destination not in mount_points() and not os.path.ismount(destination),
            'CNSS mountpoint already belongs to another source: ' + str(destination))
    subprocess.run(['/usr/bin/mount', '--bind', str(source), str(destination)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
    require(same_directory(source, destination), 'CNSS bind mount identity mismatch')


def prepare():
    require(os.geteuid() == 0, 'CNSS preparation requires root inside native Ubuntu')
    RUN.mkdir(mode=0o755, exist_ok=True)
    directory(RUN)
    fd = os.open(RUN / 'cnss-prepare.lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        verify_runtime(RUNTIME, MANIFEST)
        # This data tree belongs to the private runtime; Android /data is never
        # mounted or copied. Refuse existing mounts and any symlink component.
        points = mount_points()
        for relative in ('data', 'data/vendor', 'data/vendor/wifi', 'data/vendor/wifi/sockets'):
            path = RUNTIME / relative
            require(path not in points and not os.path.ismount(path),
                    'CNSS private data directory must not be a mount: ' + relative)
            path.mkdir(mode=0o755, exist_ok=True)
            directory(path)
        for name in ('dev', 'proc', 'sys'):
            bind_directory(Path('/' + name), RUNTIME / name)
    return RUNTIME


if __name__ == '__main__':
    print('CNSS runtime verified; kernel views prepared:', prepare())
