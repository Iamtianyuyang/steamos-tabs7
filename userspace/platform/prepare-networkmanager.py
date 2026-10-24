#!/usr/bin/python3
"""Verify the fixed private NetworkManager payload; return its executable."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys

PREFIX = Path('/usr/local/lib/tabs7linux/networkmanager-1.46')
VERSION = '1.46.0-1ubuntu2.8'
DIST_VERSION = VERSION + '+tabs7linux-zero-gateway'
PATCHED_SHA = 'baa746045c1aa9ed2118d22db3be9364d16c4f1717b7e68de30b5b14ca231f2d'
MANIFEST_NAME = 'tabs7linux-build.json'
REQUIRED = {'sbin/NetworkManager',
            'lib/NetworkManager/' + DIST_VERSION + '/libnm-device-plugin-wifi.so'}


def protected(path):
    info = path.lstat()
    if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
        raise RuntimeError('Private NM path is not protected/root-owned: ' + str(path))
    return info


def records(document):
    if (not isinstance(document, dict) or document.get('source_version') != VERSION or
            document.get('dist_version') != DIST_VERSION or
            document.get('platform_source_sha256') != PATCHED_SHA or
            document.get('libnm_deployed') is not False):
        raise RuntimeError('Private NM build manifest does not match the verified patch/version')
    files = document.get('files')
    if not isinstance(files, list) or not 2 <= len(files) <= 10000:
        raise RuntimeError('Invalid private NM manifest files list')
    result = {}
    for record in files:
        if not isinstance(record, dict):
            raise RuntimeError('Invalid private NM manifest record')
        path, size, digest = (record.get(k) for k in ('path', 'size', 'sha256'))
        if (not isinstance(path, str) or not path or '\\' in path or '\x00' in path or
                PurePosixPath(path).is_absolute() or
                any(part in ('', '.', '..') for part in path.split('/')) or
                path == MANIFEST_NAME or path in result or
                type(size) is not int or not 0 < size <= 256 * 1024 * 1024 or
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)):
            raise RuntimeError('Invalid or duplicate private NM file record')
        if re.fullmatch(r'libnm\.so(?:\..*)?', PurePosixPath(path).name):
            raise RuntimeError('Private NM payload must not deploy a client libnm ABI')
        result[path] = (size, digest)
    if not REQUIRED <= result.keys():
        raise RuntimeError('Private NM daemon or Wi-Fi plugin is missing from the manifest')
    return result


def verify_file(path, size, digest, require_elf=False):
    info = protected(path)
    if not stat.S_ISREG(info.st_mode) or info.st_size != size:
        raise RuntimeError('Private NM file is not the expected regular file: ' + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or
                opened.st_uid != 0 or opened.st_mode & 0o022 or opened.st_size != size):
            raise RuntimeError('Private NM file changed before verification: ' + str(path))
        hasher, count, header = hashlib.sha256(), 0, b''
        while True:
            data = os.read(fd, 1024 * 1024)
            if not data:
                break
            if not header:
                header = data[:64]
            hasher.update(data)
            count += len(data)
            if count > size:
                raise RuntimeError('Private NM file grew during verification: ' + str(path))
        if count != size or hasher.hexdigest() != digest:
            raise RuntimeError('Private NM checksum mismatch: ' + str(path))
        if require_elf and (len(header) < 64 or header[:7] != b'\x7fELF\x02\x01\x01' or
                            struct.unpack_from('<H', header, 18)[0] != 183):
            raise RuntimeError('Private NM daemon/plugin must be AArch64 ELF64: ' + str(path))
    finally:
        os.close(fd)


def prepare():
    if os.getuid() != 0 or os.geteuid() != 0:
        raise RuntimeError('Verify private NetworkManager as native Ubuntu root')
    for directory in reversed([PREFIX, *PREFIX.parents]):
        info = protected(directory)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('Private NM prefix ancestors must be real directories')
    manifest = PREFIX / MANIFEST_NAME
    info = protected(manifest)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4 * 1024 * 1024:
        raise RuntimeError('Private NM manifest is not a bounded regular file')
    fd = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with os.fdopen(fd, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or
                    opened.st_uid != 0 or opened.st_mode & 0o022):
                raise RuntimeError('Private NM manifest changed before reading')
            data = stream.read(4 * 1024 * 1024 + 1)
            if len(data) != info.st_size:
                raise RuntimeError('Private NM manifest size changed')
    except Exception:
        # fdopen owns the descriptor after entering its context.
        raise
    expected = records(json.loads(data))
    found = set()
    for current, directories, files in os.walk(PREFIX, followlinks=False):
        for name in [*directories, *files]:
            path = Path(current) / name
            info = protected(path)
            if re.fullmatch(r'libnm\.so(?:\..*)?', name):
                raise RuntimeError('Unexpected client libnm inside the private NM payload')
            if stat.S_ISLNK(info.st_mode):
                target = path.resolve(strict=True)
                if not target.is_relative_to(PREFIX):
                    raise RuntimeError('Private NM symlink escapes its prefix: ' + str(path))
            elif stat.S_ISREG(info.st_mode):
                relative = str(path.relative_to(PREFIX))
                if relative == MANIFEST_NAME:
                    continue
                if relative not in expected:
                    raise RuntimeError('Private NM file is not in its manifest: ' + relative)
                size, digest = expected[relative]
                verify_file(path, size, digest, relative in REQUIRED)
                found.add(relative)
            elif not stat.S_ISDIR(info.st_mode):
                raise RuntimeError('Unexpected private NM file type: ' + str(path))
    if found != expected.keys():
        raise RuntimeError('Some private NM manifest files are missing/not regular')
    executable = PREFIX / 'sbin/NetworkManager'
    if executable.stat().st_mode & 0o111 != 0o111:
        raise RuntimeError('Private NetworkManager executable mode is incomplete')
    return executable


if __name__ == '__main__':
    try:
        print(prepare())
    except (OSError, RuntimeError, ValueError, TypeError, struct.error) as error:
        print('Private NetworkManager verification failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
