#!/usr/bin/python3
"""Verify matched IPA firmware, then request one native IPA initialization.

Run as root inside Ubuntu. This uses the stock /dev/ipa userspace command,
not register access. Success proves primary initialization and uC PIL reset;
the separate uC ready state is reported only when its kernel log is observed.
"""

import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import stat
import struct
import sys
import time

MANIFEST = Path('/etc/tabs7linux/ipa-firmware.json')
FIRMWARE = Path('/usr/lib/firmware')
NATIVE_FIRMWARE = Path('/proc/1/root/linux/lib/firmware')
FW_PARAMETER = Path('/sys/module/firmware_class/parameters/path')
IPA_DEVICE = Path('/dev/ipa')
IPA_SYS_DEV = Path('/sys/class/ipa/ipa/dev')
RUN = Path('/run/network')
MARKER = RUN / 'ipa-attempted'
KERNEL_LOG = RUN / 'ipa-kernel.log'
NAME = re.compile(r'ipa_(?:fws|uc)\.(?:mdt|b[0-9]{2})\Z')
IPA_MESSAGE = re.compile(r'\bipa(?:3)?(?:[_ :])|qcom,ipa', re.I)
IPA_FAILURE = re.compile(r'fail(?:ed|ure)?|error|unable|bad parm|bad param|'
                         r'panic|\bBUG\b|timeout|timed out', re.I)


def protected_file(path, limit):
    """Read a bounded root-owned regular file without following its leaf."""
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                info.st_mode & 0o022 or info.st_size > limit):
            raise RuntimeError('Not a protected regular file: ' + str(path))
        data = bytearray()
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise RuntimeError('File grew beyond its limit: ' + str(path))
        if len(data) != info.st_size:
            raise RuntimeError('File size changed while reading: ' + str(path))
        return bytes(data)
    finally:
        os.close(fd)


def mdt_segments(data, prefix):
    """Match this kernel's ELF32 peripheral-loader program-header rules."""
    if len(data) < 52 or data[:7] != b'\x7fELF\x01\x01\x01':
        raise RuntimeError(prefix + ': expected ELF32 little-endian MDT')
    fields = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    machine, version, phoff = fields[1], fields[2], fields[4]
    ehsize, phentsize, phnum = fields[7:10]
    if (version != 1 or ehsize != 52 or phoff != 52 or phentsize != 32 or
            not 1 <= phnum <= 100 or 52 + phnum * 32 > len(data)):
        raise RuntimeError(prefix + ': invalid or out-of-bounds MDT headers')
    required, allowed = {}, {}
    for index in range(phnum):
        kind, _, _, _, filesz, memsz, flags, _ = struct.unpack_from(
            '<IIIIIIII', data, 52 + index * 32)
        name = f'{prefix}.b{index:02d}'
        if filesz:
            allowed[name] = filesz
        is_hash = (flags & (7 << 24)) == (2 << 24)
        if kind == 1 and not is_hash and memsz:
            if filesz > memsz:
                raise RuntimeError(prefix + ': load segment exceeds memory size')
            if filesz:
                required[name] = filesz
    if not required:
        raise RuntimeError(prefix + ': no external load segments')
    return {'machine': machine, 'phnum': phnum, 'required': required,
            'allowed': allowed}


def verify_firmware():
    raw = protected_file(MANIFEST, 1024 * 1024)
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise RuntimeError('Invalid IPA firmware manifest object')
    records = document.get('files')
    if not isinstance(records, list) or not 4 <= len(records) <= 202:
        raise RuntimeError('Invalid IPA firmware manifest files list')
    root = FIRMWARE.resolve(strict=True)
    info = root.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('IPA firmware directory is not protected')
    native = NATIVE_FIRMWARE.stat()
    if (info.st_dev, info.st_ino) != (native.st_dev, native.st_ino):
        raise RuntimeError('Native kernel and Ubuntu firmware views differ')
    if FW_PARAMETER.read_bytes() != b'/linux/lib/firmware\n':
        raise RuntimeError('Firmware search path must be /linux/lib/firmware without embedded LF')
    contents, sizes = {}, {}
    for record in records:
        if not isinstance(record, dict):
            raise RuntimeError('Invalid IPA firmware record')
        name, size, digest = (record.get(k) for k in ('path', 'size', 'sha256'))
        source = record.get('source')
        if (not isinstance(name, str) or not NAME.fullmatch(name) or name in sizes or
                type(size) is not int or not 0 < size <= 16 * 1024 * 1024 or
                not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest) or
                not isinstance(source, str) or not source or len(source) > 1024 or
                any(c in source for c in '\x00\n\r')):
            raise RuntimeError('Invalid or duplicate IPA firmware manifest record')
        data = protected_file(root / name, size)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError('IPA firmware size/checksum mismatch: ' + name)
        sizes[name] = size
        if name.endswith('.mdt'):
            contents[name] = data
    segments = {}
    for prefix in ('ipa_fws', 'ipa_uc'):
        if prefix + '.mdt' not in contents:
            raise RuntimeError('Missing MDT: ' + prefix)
        parsed = mdt_segments(contents[prefix + '.mdt'], prefix)
        for name, size in parsed['required'].items():
            if sizes.get(name) != size:
                raise RuntimeError('Missing/wrong-size MDT load segment: ' + name)
        for name, size in sizes.items():
            if name.startswith(prefix + '.b') and parsed['allowed'].get(name) != size:
                raise RuntimeError('Blob does not match an MDT program header: ' + name)
        segments[prefix] = {'machine': parsed['machine'], 'phnum': parsed['phnum'],
                            'load_segments': sorted(parsed['required'])}
    return {'manifest_sha256': hashlib.sha256(raw).hexdigest(),
            'files': sorted(sizes), 'segments': segments}


def verify_device(fd=None):
    info = os.fstat(fd) if fd is not None else IPA_DEVICE.lstat()
    if (not stat.S_ISCHR(info.st_mode) or info.st_uid != 0 or
            (os.major(info.st_rdev), os.minor(info.st_rdev)) != (245, 0) or
            IPA_SYS_DEV.read_bytes() != b'245:0\n'):
        raise RuntimeError('IPA char device does not match current sysfs 245:0')


def save_marker(fd, result):
    data = (json.dumps(result, sort_keys=True) + '\n').encode()
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    while data:
        count = os.write(fd, data)
        if count <= 0:
            raise RuntimeError('Could not update IPA attempt marker')
        data = data[count:]
    os.fsync(fd)


def wait_initialized(fd, output, timeout):
    """Consume consecutive new kmsg records; uC PIL reset is not uC ready."""
    evidence = {'primary_fw_loaded': False, 'primary_initialized': False,
                'uc_pil_reset': False, 'uc_ready_observed': False}
    deadline, settled, previous = time.monotonic() + timeout, None, None
    poll = select.poll()
    poll.register(fd, select.POLLIN | select.POLLERR)
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise RuntimeError('IPA initialization timed out; observed ' + json.dumps(evidence))
        try:
            record = os.read(fd, 1024 * 1024)
        except BlockingIOError:
            # Drain all available records before declaring success: a queued
            # uC/PIL failure must not be hidden by the settling deadline.
            if settled is not None and time.monotonic() >= settled + 1.0:
                return evidence
            poll.poll(max(1, min(100, int((deadline - now) * 1000))))
            continue
        except OSError as error:
            if error.errno == errno.EPIPE:
                raise RuntimeError('Kernel log overran; cannot certify IPA initialization') from error
            raise
        if not record:
            raise RuntimeError('Kernel log unexpectedly reached EOF')
        output.write(record)
        output.flush()
        header, separator, body = record.partition(b';')
        parts = header.split(b',')
        if not separator or len(parts) < 3:
            raise RuntimeError('Malformed kernel log record')
        sequence = int(parts[1])
        if previous is not None and sequence != previous + 1:
            raise RuntimeError('Kernel log sequence gap; cannot certify IPA initialization')
        previous = sequence
        message = body.decode('utf-8', 'replace')
        if IPA_MESSAGE.search(message) and IPA_FAILURE.search(message):
            raise RuntimeError('IPA kernel initialization error: ' + message.strip())
        if 'IPA FW loaded successfully' in message:
            evidence['primary_fw_loaded'] = True
        if 'IPA driver initialization was successful.' in message:
            evidence['primary_initialized'] = True
        if re.search(r'\bipa_uc\b.*Brought out of reset', message):
            evidence['uc_pil_reset'] = True
        if 'IPA uC loaded' in message:
            evidence['uc_ready_observed'] = True
        if (settled is None and evidence['primary_fw_loaded'] and
                evidence['primary_initialized'] and evidence['uc_pil_reset']):
            settled = time.monotonic()


def prepare(timeout=30.0, verify_only=False):
    if os.geteuid() != 0:
        raise RuntimeError('Run IPA preparation as root inside native Ubuntu')
    if not math.isfinite(timeout) or not 2 <= timeout <= 60:
        raise RuntimeError('IPA timeout must be between 2 and 60 seconds')
    result = verify_firmware()
    verify_device()
    if verify_only:
        return dict(result, status='verified', initialization_attempted=False)
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = RUN.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Network runtime directory is not protected')
    if MARKER.exists() or MARKER.is_symlink():
        raise RuntimeError('IPA was already attempted this boot; refusing another write')
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    log_fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    marker_fd = ipa_fd = None
    try:
        if not stat.S_ISCHR(os.fstat(log_fd).st_mode):
            raise RuntimeError('/dev/kmsg is not a character device')
        os.lseek(log_fd, 0, os.SEEK_END)
        marker_fd = os.open(MARKER, os.O_RDWR | os.O_CREAT | os.O_EXCL |
                            os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        result.update(status='attempted', boot_id=boot_id,
                      started_monotonic=time.monotonic(), kernel_log=str(KERNEL_LOG))
        save_marker(marker_fd, result)
        output_fd = os.open(KERNEL_LOG, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                            os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(output_fd, 'wb') as output:
            ipa_fd = os.open(IPA_DEVICE, os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            verify_device(ipa_fd)
            if FW_PARAMETER.read_bytes() != b'/linux/lib/firmware\n':
                raise RuntimeError('Firmware search path changed before IPA request')
            if os.write(ipa_fd, b'1') != 1:
                raise RuntimeError('IPA initialization command was not accepted in full')
            os.close(ipa_fd)
            ipa_fd = None
            result.update(wait_initialized(log_fd, output, timeout))
            os.fsync(output.fileno())
        result.update(status='primary-initialized', completed_monotonic=time.monotonic())
        save_marker(marker_fd, result)
        return result
    except Exception as error:
        if marker_fd is not None:
            result.update(status='failed', error=str(error))
            save_marker(marker_fd, result)
        raise
    finally:
        if ipa_fd is not None:
            os.close(ipa_fd)
        if marker_fd is not None:
            os.close(marker_fd)
        os.close(log_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-only', action='store_true', help='verify without requesting initialization')
    parser.add_argument('--timeout', type=float, default=30.0)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.timeout, args.verify_only), sort_keys=True))
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, struct.error) as error:
        print('IPA preparation failed: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
