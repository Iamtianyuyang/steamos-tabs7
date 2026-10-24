#!/usr/bin/python3
"""Give the native desktop a per-boot /tmp without hiding live services."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess

SOURCE = Path('/run/tabs7linux-tmp')
TARGET = Path('/tmp')


def identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino


def require_native_root():
    if os.geteuid() != 0:
        raise RuntimeError('Runtime preparation requires root inside Ubuntu')
    if identity(Path('/run')) != identity(Path('/proc/1/root/run')):
        raise RuntimeError('Ubuntu /run must use the native per-boot directory')


def mount_points():
    return [re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
            for line in Path('/proc/self/mountinfo').read_text().splitlines()]


def socket_aliases(source, target):
    aliases = {str(target), str(source)}
    target_inode = identity(target)
    # Native callers may bind using the rootfs bind or its persistent source.
    for name in ('/linux/tmp', '/mnt/native-data/tabs7linux/ubuntu/tmp'):
        try:
            if identity(Path('/proc/1/root' + name)) == target_inode:
                aliases.add(name)
        except FileNotFoundError:
            pass
    return aliases


def reject_live_sockets(aliases):
    for line in Path('/proc/net/unix').read_text().splitlines()[1:]:
        fields = line.split(maxsplit=7)
        if len(fields) == 8:
            name = fields[7].lstrip('@')
            if any(name.startswith(alias + '/') for alias in aliases):
                raise RuntimeError('A live temporary-directory socket prevents its replacement: ' + fields[7])


def caller():
    pid = os.getppid()
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()
    return [pid, int(fields[19])]


def record_directory(path):
    if path.parent != Path('/run/phosh') or path.parent.resolve() != path.parent:
        raise RuntimeError('Bind record must be in the managed /run/phosh directory')
    info = path.parent.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError('Bind record directory must be protected and root-owned')


def prepare(record=None):
    require_native_root()
    source, target = SOURCE, TARGET
    for path in (source, target):
        if path.is_symlink():
            raise RuntimeError('Runtime directory must not be a symlink: ' + str(path))
        path.mkdir(mode=0o1777, exist_ok=True)
        if not path.is_dir() or path.stat().st_uid != 0:
            raise RuntimeError('Runtime directory must be root-owned: ' + str(path))
    if identity(source) == identity(target):
        return
    if any(point == '/tmp' or point.startswith('/tmp/') for point in mount_points()):
        raise RuntimeError('Existing /tmp mount or child mount does not belong to this runtime')
    reject_live_sockets(socket_aliases(source, target))
    source.chmod(0o1777)
    if record is not None:
        record_directory(record)
        data = {'version': 1, 'caller': caller(),
                'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                'source_inode': identity(source), 'previous_target_inode': identity(target)}
        # Record intent before mount so a helper failure after mount remains
        # recoverable. Cleanup never acts unless the target is this source.
        descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(data, output)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
    subprocess.run(['/usr/bin/mount', '--bind', str(source), str(target)],
                   check=True, timeout=10)
    if identity(source) != identity(target):
        raise RuntimeError('Per-boot /tmp bind identity mismatch')
    print('Desktop /tmp uses the native per-boot runtime directory.')


def cleanup(record, keep_mounted=False):
    require_native_root()
    record_directory(record)
    try:
        info = record.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077 or info.st_size > 4096:
        raise RuntimeError('Bind record must be a private root-owned regular file')
    data = json.loads(record.read_text())
    if (data.get('version') != 1 or data.get('caller') != caller() or
            data.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
        raise RuntimeError('Bind record is not owned by this startup')
    if keep_mounted:
        record.unlink()
        return
    points = mount_points()
    if '/tmp' not in points and list(identity(TARGET)) == data['previous_target_inode']:
        # Preparation failed before the mount was created.
        record.unlink()
        return
    if (SOURCE.is_symlink() or TARGET.is_symlink() or '/tmp' not in points or
            list(identity(SOURCE)) != data['source_inode'] or
            list(identity(TARGET)) != data['source_inode']):
        raise RuntimeError('Refusing to unmount an unrelated temporary directory')
    if any(point.startswith('/tmp/') for point in points):
        raise RuntimeError('A temporary-directory child mount prevents cleanup')
    reject_live_sockets(socket_aliases(SOURCE, TARGET))
    subprocess.run(['/usr/bin/umount', str(TARGET)], check=True, timeout=10)
    if list(identity(TARGET)) != data['previous_target_inode']:
        raise RuntimeError('Temporary-directory identity after cleanup does not match its previous directory')
    record.unlink()
    print('Removed this failed startup\'s temporary-directory bind; underlying files were retained.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--record-new-bind', type=Path)
    mode.add_argument('--cleanup-new-bind', type=Path)
    parser.add_argument('--keep-mounted', action='store_true')
    args = parser.parse_args()
    if args.keep_mounted and args.cleanup_new_bind is None:
        parser.error('--keep-mounted requires --cleanup-new-bind')
    if args.cleanup_new_bind is not None:
        cleanup(args.cleanup_new_bind, args.keep_mounted)
    else:
        prepare(args.record_new_bind)
