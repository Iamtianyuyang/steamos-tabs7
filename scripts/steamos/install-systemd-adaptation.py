#!/usr/bin/env python3
"""Install (or remove) the SM-T870 systemd adaptation into a SteamOS tree.

Run as root from the native Ubuntu side against the separate SteamOS root.
Every replaced path is backed up and recorded, so --uninstall restores the
tree exactly. The marker written last is what the initramfs requires before
it will switch root into this tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

SOURCE = Path(__file__).resolve().parents[2] / 'userspace/steamos/systemd'
SESSION = SOURCE.parent / 'gamescope-session'
# DPU VIG plane IDs of this kernel's DSI-1 scanout (wlroots/gamescope split).
SPLIT_PLANES = '65,86'
GPU_FIRMWARE = {'a650_sqe.fw', 'a650_gmu.bin', 'a650_zap.mdt', 'a650_zap.b00', 'a650_zap.b01', 'a650_zap.b02'}
BUILD_ID = '20261002.6232440'
BUSYBOX_SHA256 = '52151e7f322f926b64049cdaa1410dc3ea6485525e0624b05813791c219ae933'
MARKER = 'TABS7LINUX_STEAMOS_ADAPTATION=1\nMODEL=SM-T870\nARCH=arm64\nINIT=/usr/lib/systemd/systemd'
STATE = 'var/lib/tabs7linux/adaptation'
UNIT_DIRS = ('etc/systemd/system', 'usr/lib/systemd/system')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Tree:
    def __init__(self, root):
        self.root = root
        self.state = root / STATE
        manifest = self.state / 'manifest.json'
        self.manifest = json.loads(manifest.read_text()) if manifest.exists() else {'entries': {}}

    def path(self, relative):
        target = self.root / relative
        # Refuse symlinked parents: every write must stay inside this tree.
        if target.parent.resolve() != (self.root.resolve() / relative).parent:
            raise ValueError('Path escapes the SteamOS tree: ' + relative)
        return target

    def remember(self, relative):
        """Back up the original once, before the first change to a path."""
        if relative in self.manifest['entries']:
            return
        target = self.path(relative)
        entry = {'existed': target.exists() or target.is_symlink()}
        if target.is_symlink():
            entry['symlink'] = os.readlink(target)
        elif target.exists():
            backup = self.state / 'backup' / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            entry['backup'] = str(backup.relative_to(self.root))
        self.manifest['entries'][relative] = entry

    def write(self, relative, data, mode=0o644):
        self.remember(relative)
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            target.unlink()
        temporary = target.with_name(target.name + '.tabs7-new')
        temporary.write_bytes(data)
        temporary.chmod(mode)
        os.replace(temporary, target)

    def link(self, relative, destination):
        self.remember(relative)
        target = self.path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        target.symlink_to(destination)

    def save(self):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'manifest.json').write_text(json.dumps(self.manifest, indent=2) + '\n')


def masked_units():
    units = []
    for line in (SOURCE / 'masked-units.txt').read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            units.append(line)
    return units


def gpu_firmware(manifest, directory):
    records = json.loads(manifest.read_text())['firmware']
    if {record['path'] for record in records} != GPU_FIRMWARE or len(records) != len(GPU_FIRMWARE):
        raise ValueError('Unexpected Adreno 650 firmware set')
    files = {}
    for record in records:
        source = directory / record['path']
        if source.is_symlink() or not source.is_file() or source.stat().st_size != record['size']:
            raise ValueError('GPU firmware is not the verified regular file: ' + record['path'])
        if sha(source) != record['sha256']:
            raise ValueError('GPU firmware checksum mismatch: ' + record['path'])
        files[record['path']] = source.read_bytes()
    return files


def shadow_files(root):
    """Locked shadow/gshadow like pwconv/grpconv. Stock SteamOS creates them
    in its /etc overlay on first boot; this port uses a plain /etc, and PAM's
    account phase needs an entry. '!*' disables password login for everyone."""
    files = {}
    if not (root / 'etc/shadow').exists():
        users = [line.split(':')[0] for line in (root / 'etc/passwd').read_text().splitlines() if line]
        files['etc/shadow'] = ''.join(f'{name}:!*:20000::::::\n' for name in users).encode()
    if not (root / 'etc/gshadow').exists():
        groups = [line.split(':') for line in (root / 'etc/group').read_text().splitlines() if line]
        files['etc/gshadow'] = ''.join(f'{fields[0]}:!*::{fields[3]}\n' for fields in groups).encode()
    return files


def session_os_release(root):
    """os-release for the Game Mode session only: no VR variant."""
    lines = (root / 'usr/lib/os-release').read_text().splitlines(keepends=True)
    kept = [line for line in lines if not line.startswith(('VARIANT=', 'VARIANT_ID='))]
    if kept == lines:
        raise ValueError('os-release no longer carries the VR variant; review the session override')
    return ''.join(kept).encode()


def install(tree, busybox, game_mode=None):
    if sha(busybox) != BUSYBOX_SHA256:
        raise ValueError('BusyBox does not match the pinned static ARM64 build')
    tree.write('usr/local/lib/tabs7linux/bin/busybox', busybox.read_bytes(), 0o755)
    tree.write('etc/systemd/system/tabs7-debug-shell.service', (SOURCE / 'tabs7-debug-shell.service').read_bytes())
    for target in ('sysinit', 'emergency', 'rescue'):
        tree.link(f'etc/systemd/system/{target}.target.wants/tabs7-debug-shell.service',
                  '/etc/systemd/system/tabs7-debug-shell.service')
    tree.write('etc/NetworkManager/conf.d/50-tabs7-usb0.conf', (SOURCE / '50-tabs7-usb0.conf').read_bytes())
    tree.write('etc/systemd/network/10-tabs7-usb0.link', (SOURCE / '10-tabs7-usb0.link').read_bytes())
    tree.write('etc/systemd/journald.conf.d/50-tabs7-persistent.conf', (SOURCE / '50-tabs7-persistent.conf').read_bytes())
    absent = []
    for unit in masked_units():
        if not any((tree.root / directory / unit).exists() for directory in UNIT_DIRS):
            absent.append(unit)
        tree.link('etc/systemd/system/' + unit, '/dev/null')
    for relative, data in shadow_files(tree.root).items():
        tree.write(relative, data, 0o600)
    if game_mode is None:
        # Layer 1: console boot only.
        tree.link('etc/systemd/system/default.target', '/usr/lib/systemd/system/multi-user.target')
    else:
        for name, data in game_mode.items():
            tree.write('usr/lib/firmware/' + name, data)
        tree.write('etc/udev/rules.d/70-tabs7-kgsl.rules', (SOURCE / '70-tabs7-kgsl.rules').read_bytes())
        tree.write('etc/systemd/system/seatd.service.d/50-tabs7-novt.conf',
                   (SOURCE / '50-tabs7-seatd-novt.conf').read_bytes())
        tree.write('etc/tabs7linux/sm-t870-split', SPLIT_PLANES.encode() + b'\n')
        tree.write('usr/local/lib/tabs7linux/steamos/gamescope-session', SESSION.read_bytes(), 0o755)
        tree.write('usr/local/lib/tabs7linux/steamos/os-release', session_os_release(tree.root))
        tree.write('etc/systemd/system/tabs7-gamescope-session.service',
                   (SOURCE / 'tabs7-gamescope-session.service').read_bytes())
        tree.link('etc/systemd/system/graphical.target.wants/tabs7-gamescope-session.service',
                  '/etc/systemd/system/tabs7-gamescope-session.service')
        tree.link('etc/systemd/system/default.target', '/usr/lib/systemd/system/graphical.target')
    tree.write('etc/tabs7linux/steamos-adaptation', MARKER.encode())
    tree.save()
    return absent


def uninstall(tree):
    # Marker first: the initramfs stops selecting this tree immediately.
    for relative, entry in sorted(tree.manifest['entries'].items(),
                                  key=lambda item: item[0] != 'etc/tabs7linux/steamos-adaptation'):
        target = tree.path(relative)
        if target.is_symlink() or target.exists():
            target.unlink()
        if 'symlink' in entry:
            target.symlink_to(entry['symlink'])
        elif 'backup' in entry:
            shutil.copy2(tree.root / entry['backup'], target)
    tree.manifest = {'entries': {}}
    tree.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='SteamOS tree, e.g. /steamos')
    parser.add_argument('--busybox', type=Path, help='pinned static BusyBox for the debug shell')
    parser.add_argument('--uninstall', action='store_true')
    parser.add_argument('--game-mode', action='store_true',
                        help='also install GPU firmware, KGSL access and the gamescope session')
    parser.add_argument('--gpu-firmware-manifest', type=Path, help='verified gpu-firmware.json (Ubuntu side)')
    parser.add_argument('--gpu-firmware-dir', type=Path, help='directory holding the manifest files')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('run as root')
    root = args.root.resolve()
    release = (root / 'usr/lib/os-release').read_text()
    if 'ID=steamos\n' not in release or f'BUILD_ID={BUILD_ID}\n' not in release:
        raise ValueError('Not the pinned SteamOS build')
    tree = Tree(root)
    if args.uninstall:
        uninstall(tree)
        print('SM-T870 adaptation removed; the initramfs will no longer select this tree')
        return
    if args.busybox is None:
        parser.error('--busybox is required to install')
    game_mode = None
    if args.game_mode:
        if args.gpu_firmware_manifest is None or args.gpu_firmware_dir is None:
            parser.error('--game-mode needs --gpu-firmware-manifest and --gpu-firmware-dir')
        game_mode = gpu_firmware(args.gpu_firmware_manifest, args.gpu_firmware_dir)
    absent = install(tree, args.busybox, game_mode)
    print(json.dumps({'installed': True, 'changed_paths': len(tree.manifest['entries']),
                      'masked_units_absent_in_tree': absent}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        sys.exit(f'SteamOS adaptation failed: {error}')
