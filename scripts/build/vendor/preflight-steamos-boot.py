#!/usr/bin/env python3
"""Preflight for a SteamOS BOOT write, followed by the offline rebuild.

Never accesses a device. Checks, in order:

1. the same-boot partition snapshot: manifest hashes, PIT capacity, the
   DTBO/RECOVERY layout, the live DTB reservations and the board/msm id the
   hardware actually reported;
2. the currently deployed BOOT: AVB signature, header v2, ramdisk extraction;
3. the initramfs review rebuild: only ``init`` and ``sbin/start-persistent-gui``
   may change;
4. the ramdisk-only BOOT rebuild: header, kernel, DTB and command line are
   preserved byte for byte.

Every artefact stays under ``out/``. Flashing remains a separate step that
needs the operator's authorization and a readback comparison.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARTITIONS = ('boot', 'dtbo', 'recovery', 'vbmeta')
SNAPSHOT_ROOTS = (ROOT / 'out/steamos', ROOT / 'out/device')
EXPECTED_BOARD_ID = [8, 7]           # qcom,board-id from the downstream overlay r07
EXPECTED_MSM_ID = [356, 131073]      # qcom,msm-id 0x20001


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def display(path):
    """Project-relative name when possible, absolute otherwise (test fixtures)."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def snapshot_manifest(snapshot):
    manifest_path = snapshot / 'manifest.json'
    if not manifest_path.is_file():
        raise ValueError(f'snapshot has no manifest.json: {snapshot}')
    manifest = json.loads(manifest_path.read_text())
    if 'partitions' not in manifest:
        raise ValueError(f'manifest is not a partition snapshot: {manifest_path}')
    return manifest


def verify_snapshot(snapshot, manifest, profile, layout):
    """Check a same-boot snapshot; return {name: path} for the four partitions."""
    paths = {}
    for name in PARTITIONS:
        path = snapshot / f'{name}.img'
        if not path.is_file():
            raise ValueError(f'snapshot is missing {name}.img: {snapshot}')
        recorded = manifest['partitions'].get(name, {}).get('sha256')
        if recorded != file_sha(path):
            raise ValueError(f'{name}.img does not match its manifest hash: {path}')
        capacity = profile['device_pit']['partitions'][name.upper()]['capacity_bytes']
        if path.stat().st_size != capacity:
            raise ValueError(f'{name}.img is not a complete partition read: {path}')
        paths[name] = path
    for name, expected in layout.items():
        if file_sha(paths[name]) != expected:
            raise ValueError(f'{name.upper()} is not in the expected SteamOS migration layout')
    live = snapshot / 'live.dtb'
    recorded = manifest.get('live.dtb') or {}
    if not live.is_file() or recorded.get('sha256') != file_sha(live):
        raise ValueError('the live DTB of the snapshot is missing or changed')
    if manifest.get('qcom,board-id') != EXPECTED_BOARD_ID:
        raise ValueError(f'board-id is {manifest.get("qcom,board-id")}, expected {EXPECTED_BOARD_ID}')
    if manifest.get('qcom,msm-id') != EXPECTED_MSM_ID:
        raise ValueError(f'msm-id is {manifest.get("qcom,msm-id")}, expected {EXPECTED_MSM_ID}')
    return {'paths': paths, 'live_dtb': live, 'boot_id': manifest.get('boot_id')}


def find_snapshot(layout):
    """Return the only candidate snapshot that passes the structural checks."""
    candidates = []
    for root in SNAPSHOT_ROOTS:
        if not root.is_dir():
            continue
        try:
            directories = sorted(root.iterdir())
        except OSError as error:
            print(f'note: skipping unreadable snapshot root {display(root)}: {error}', file=sys.stderr)
            continue
        for directory in directories:
            try:
                if not (directory / 'manifest.json').is_file():
                    continue
                manifest = snapshot_manifest(directory)
                if set(manifest['partitions']) < set(PARTITIONS):
                    continue
                if all((directory / f'{name}.img').is_file() for name in PARTITIONS) and \
                        all(file_sha(directory / f'{name}.img') == expected
                            for name, expected in layout.items()):
                    candidates.append(directory)
            except (OSError, ValueError):
                continue
    if len(candidates) != 1:
        listing = ', '.join(display(path) for path in candidates) or 'none'
        raise ValueError(f'pass --snapshot explicitly: matching snapshots: {listing}')
    return candidates[0]


def ensure_under_out(path):
    out = (ROOT / 'out').resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(out):
        raise ValueError('preflight output must stay under the project out/ directory')
    return resolved


def assemble_backup_view(paths, deployed_boot, manifest, destination):
    """Build a same-boot view whose boot.img is the currently deployed image."""
    destination.mkdir(parents=True, exist_ok=False)
    evidence = {'kind': 'preflight backup view', 'partitions': {}, 'boot_id': manifest.get('boot_id'),
                'provenance': {'boot': 'deployed image', 'dtbo': 'snapshot', 'recovery': 'snapshot',
                               'vbmeta': 'snapshot', 'snapshot': display(paths['boot'].parent)}}
    for name in PARTITIONS:
        source = deployed_boot if name == 'boot' else paths[name]
        target = destination / f'{name}.img'
        try:
            os.link(source, target)
        except OSError:
            shutil.copyfile(source, target)
        evidence['partitions'][name] = {'sha256': file_sha(target), 'bytes': target.stat().st_size}
    (destination / 'manifest.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


def tool_paths():
    return {'unpack': ROOT / 'out/tools/mkbootimg/unpack_bootimg.py',
            'mkbootimg': ROOT / 'out/tools/mkbootimg/mkbootimg.py',
            'avbtool': ROOT / 'out/tools/avb/avbtool.py',
            'key': ROOT / 'out/tools/avb/testkey_rsa4096.pem',
            'debs': ROOT / 'out/tools/debs'}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--deployed-boot', type=Path, required=True,
                        help='BOOT image that is on the device now (its ramdisk is the review reference)')
    parser.add_argument('--snapshot', type=Path,
                        help='same-boot snapshot with boot/dtbo/recovery/vbmeta (auto-detected when unique)')
    parser.add_argument('--output', type=Path, required=True, help='directory under out/ for this preflight')
    parser.add_argument('--dry-run', action='store_true', help='run every check, build nothing')
    args = parser.parse_args()

    update = load_module('update_linux_boot', 'scripts/build/vendor/update-linux-boot.py')
    builder = load_module('vendor_builder', 'scripts/build/vendor/build-vendor-linux.py')
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json').read_text())
    if profile['model'] != 'SM-T870' or profile['hardware_revision'] != '7':
        raise ValueError('profile is not SM-T870 hardware revision 7')

    output = ensure_under_out(args.output)
    snapshot = (args.snapshot or find_snapshot(update.LAYOUT)).resolve()
    manifest = snapshot_manifest(snapshot)
    checked = verify_snapshot(snapshot, manifest, profile, update.LAYOUT)
    print(f'snapshot   : {display(snapshot)} (boot_id {checked["boot_id"]})')
    print(f'layout     : DTBO/RECOVERY match the SteamOS migration table')
    print(f'identity   : board-id {EXPECTED_BOARD_ID}, msm-id {EXPECTED_MSM_ID}, live DTB verified')
    for name in PARTITIONS:
        print(f'  {name:<9}: {file_sha(checked["paths"][name])}')

    deployed = Path(args.deployed_boot).resolve()
    deployed_sha = file_sha(deployed)
    print(f'deployed   : {deployed.name} {deployed_sha}')
    tools = tool_paths()
    missing = [display(path) for path in tools.values() if not path.exists()]
    if missing:
        raise ValueError(f'prepare the pinned tools first (scripts/setup/prepare-*): missing {missing}')
    avb = [sys.executable, str(tools['avbtool'])]
    subprocess.run(avb + ['verify_image', '--image', str(deployed), '--key', str(tools['key'])],
                   check=True, capture_output=True)
    if not list(tools['debs'].glob('busybox-static_*arm64.deb')):
        raise ValueError('the pinned BusyBox package is missing from out/tools/debs')
    print('deployed   : AVB signature verified with the project test key')

    if args.dry_run:
        print('dry run    : checks passed, nothing built')
        return

    if output.exists():
        raise ValueError(f'output already exists: {output}')
    output.mkdir(parents=True)
    unpacked = output / 'deployed'
    options = builder.unpack(tools['unpack'].parent, deployed, unpacked)
    if options.get('--header_version') != '2' or '--recovery_dtbo' in options:
        raise ValueError('deployed BOOT is not the reviewed header v2 Linux image')
    reference = output / 'reference-initramfs.cpio.gz'
    ramdisk = (unpacked / 'ramdisk').read_bytes()
    gzip.decompress(ramdisk)  # must be a gzip cpio archive
    reference.write_bytes(ramdisk)
    print(f'deployed   : header v2, ramdisk extracted ({len(ramdisk)} bytes, gzip verified)')

    backup_view = output / 'backup-view'
    evidence = assemble_backup_view(checked['paths'], deployed, manifest, backup_view)
    print(f'backup     : view assembled in {display(backup_view)} (boot = deployed image)')

    initramfs_dir = output / 'initramfs'
    subprocess.run([sys.executable, str(ROOT / 'scripts/build/vendor/build-steamos-initramfs.py'),
                    '--reference', str(reference), '--output', str(initramfs_dir)],
                   check=True, capture_output=True)
    review_bytes = (initramfs_dir / 'initramfs-review.json').read_bytes()
    review = json.loads(review_bytes)
    changed = sorted(review['changed_entries'])
    print(f'initramfs  : changed {changed}, {review["unchanged_entries"]} entries identical')

    boot_dir = output / 'boot'
    subprocess.run([sys.executable, str(ROOT / 'scripts/build/vendor/update-linux-boot.py'),
                    '--backup', str(backup_view),
                    '--ramdisk', str(initramfs_dir / 'initramfs.cpio.gz'),
                    '--ramdisk-review', str(initramfs_dir / 'initramfs-review.json'),
                    '--output', str(boot_dir)], check=True, capture_output=True)
    boot_manifest = json.loads((boot_dir / 'manifest.json').read_text())
    report = {
        'kind': 'SteamOS BOOT preflight',
        'device_write_performed': False,
        'deployed_boot': {'path': display(deployed), 'sha256': deployed_sha},
        'snapshot': {'path': display(snapshot), 'boot_id': checked['boot_id'],
                     'partitions': evidence['partitions'], 'live_dtb': file_sha(checked['live_dtb'])},
        'layout_checked': update.LAYOUT,
        'identity': {'board_id': EXPECTED_BOARD_ID, 'msm_id': EXPECTED_MSM_ID,
                     'model': profile['model'], 'hardware_revision': profile['hardware_revision']},
        'initramfs': {'reference_sha256': file_sha(reference), 'review_sha256': file_sha(
            initramfs_dir / 'initramfs-review.json'), 'changed_entries': changed},
        'boot': {'image_sha256': boot_manifest['image_sha256'], 'image_bytes': boot_manifest['image_bytes'],
                 'path': display(boot_dir / 'boot.img')},
        'next_steps': [
            'write BOOT with samloader from Download mode and compare the readback to boot.image_sha256',
            'boot once and confirm the kernel log switches root to SteamOS systemd',
            'keep backup-view/boot.img as the rollback image',
        ],
    }
    (output / 'preflight.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'boot       : {report["boot"]["path"]} {report["boot"]["image_sha256"]}')
    print(f'rollback   : {display(backup_view)}/boot.img ({deployed_sha})')
    print(f'report     : {display(output / "preflight.json")}')
    print('nothing was written to the device; flashing needs explicit authorization')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f'SteamOS BOOT preflight failed: {error}')
