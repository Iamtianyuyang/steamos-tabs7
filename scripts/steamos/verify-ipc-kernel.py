#!/usr/bin/env python3
"""Independently verify a reviewed SteamOS kernel build (IPC or systemd). Never flash."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def embedded_config(image):
    start = image.index(b'IKCFG_ST') + 8
    return gzip.decompress(image[start:image.index(b'IKCFG_ED', start)])


def exports(path):
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4:
            crc, name, _, kind = fields[:4]
            if name in result:
                raise ValueError('Duplicate exported symbol: ' + name)
            result[name] = (int(crc, 16), kind)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True,
                        help='same-boot native preflash snapshot, including modules.txt')
    parser.add_argument('--policy', choices=('steamos-ipc', 'steamos-systemd'), default='steamos-ipc')
    args = parser.parse_args()
    work, output = args.work.resolve(), args.output.resolve()
    if not output.is_relative_to(ROOT / 'out'):
        raise ValueError('Verification evidence must stay under project out/')
    output.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('repack', ROOT / 'scripts/build/vendor/repack-vendor-kernel.py')
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    build = work / 'build'
    image_path = build / 'arch/arm64/boot/Image'
    image = image_path.read_bytes()
    original = (ROOT / 'out/vendor-linux/kernel.config').read_bytes()
    if hashlib.sha256(original).hexdigest() != '02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117':
        raise ValueError('Original kernel configuration hash changed')
    candidate = (build / '.config').read_bytes()
    changes = policy.reviewed_config(original, candidate, args.policy)
    if embedded_config(image) != candidate:
        raise ValueError('Built Image does not contain the reviewed configuration')
    original_source = ROOT / 'out/vendor-kernel-prep/src/net/bluetooth/hci_sock.c'
    if sha(original_source) != '1dfdbc3320ff9001b513e136dfe665f590bcb809861b578b8bc131f5dbb9616d':
        raise ValueError('Original source changed')
    if sha(work / 'src/net/bluetooth/hci_sock.c') != '3fdb66d9e07421e5f7b61d35ce0f2729358d2dfeaf3bdd25c8311bfff45f2df8':
        raise ValueError('Reviewed Bluetooth socket repair is absent')
    old_image = ROOT / 'out/bluetooth/hci-socket-restore/artifacts/Image-hci-restored-4c2f635e6799eb292c08f1cb065d948df2cedfd45b546481410b4c2c4a05830c.bin'
    if sha(old_image) != '4c2f635e6799eb292c08f1cb065d948df2cedfd45b546481410b4c2c4a05830c':
        raise ValueError('Baseline HCI kernel hash differs')
    old_header = struct.unpack_from('<QQQ', old_image.read_bytes(), 8)
    new_header = struct.unpack_from('<QQQ', image, 8)
    if image[56:60] != b'ARM\x64' or new_header[0] != old_header[0] or new_header[2] != old_header[2]:
        raise ValueError('ARM64 Image entry layout changed')
    release = (build / 'include/config/kernel.release').read_text().strip()
    if release != '4.19.325-cip136-st20-perf-g4478511c4b37':
        raise ValueError('Unexpected kernel release')
    names = {line.split()[2] for line in (build / 'System.map').read_text().splitlines() if len(line.split()) == 3}
    required = ['__cfi_check', '__cfi_slowpath', 'hci_sock_create', 'hci_sock_bind', '__arm64_sys_semget']
    if args.policy == 'steamos-systemd':
        required += ['__arm64_sys_name_to_handle_at', 'devtmpfs_create_node', 'create_user_ns',
                     'copy_pid_ns', 'pids_cgrp_subsys', 'autofs_fill_super']
    for name in required:
        if not any(item == name or item.startswith(name + '.') for item in names):
            raise ValueError('Required kernel symbol missing: ' + name)
    trusted = exports(ROOT / 'out/references/recovered-module-abi/Module.symvers')
    rebuilt = exports(build / 'Module.symvers')
    missing = sorted(trusted.keys() - rebuilt.keys())
    changed = sorted(name for name in trusted.keys() & rebuilt.keys() if trusted[name] != rebuilt[name])
    h4 = json.loads((ROOT / 'hardware/samsung/gts7lwifi/profiles/bluetooth-h4.json').read_text())
    h4_changed = [item['name'] for item in h4['imports'] if item['name'] not in rebuilt or rebuilt[item['name']][0] != int(item['crc'], 16)]
    (output / 'export-abi-verification.json').write_text(json.dumps({'missing': missing, 'changed': changed, 'h4_imports_changed': h4_changed}, indent=2) + '\n')
    if missing:
        raise ValueError('Original exported symbols disappeared')
    # Enabling SYSVIPC adds fields to task_struct, changing CRCs throughout the
    # exported ABI. Rebuild every currently loaded external module; never force
    # an old module through version checks. This port currently loads only H4.
    backup = args.backup.resolve()
    snapshot = json.loads((backup / 'manifest.json').read_text())
    module_snapshot = backup / 'modules.txt'
    if sha(module_snapshot) != snapshot['modules.txt']['sha256']:
        raise ValueError('Loaded-module snapshot hash mismatch')
    if [line.split()[0] for line in module_snapshot.read_text().splitlines()] != ['tabs7_hci_uart']:
        raise ValueError('Unexpected loaded module set; all modules require an ABI review')
    controller_spec = importlib.util.spec_from_file_location('bluetooth', ROOT / 'userspace/platform/start-bluetooth.py')
    controller = importlib.util.module_from_spec(controller_spec)
    controller_spec.loader.exec_module(controller)
    module_path = work / 'h4/tabs7_hci_uart.ko'
    sections = controller.elf_sections(module_path.read_bytes())
    versions = sections['__versions']
    if len(versions) % 64:
        raise ValueError('Unexpected ARM64 module version record size')
    imports = {versions[i + 8:i + 64].split(b'\0', 1)[0].decode():
               struct.unpack_from('<Q', versions, i)[0]
               for i in range(0, len(versions), 64)}
    if set(imports) != {item['name'] for item in h4['imports']}:
        raise ValueError('Rebuilt H4 imported symbol set changed')
    if any(name not in rebuilt or rebuilt[name][0] != crc for name, crc in imports.items()):
        raise ValueError('Rebuilt H4 import CRC does not match candidate kernel')
    expected_config = (controller.STEAMOS_IPC_CONFIG_SHA if args.policy == 'steamos-ipc'
                       else controller.STEAMOS_SYSTEMD_CONFIG_SHA)
    if hashlib.sha256(candidate).hexdigest() != expected_config:
        raise ValueError('Candidate configuration does not match the runtime profile')
    if sha(module_path) != controller.KERNEL_MODULES[expected_config][0]:
        raise ValueError('Rebuilt module does not match the reviewed runtime profile')
    dtb = ROOT / 'out/vendor-linux/unpacked/dtb'
    dtbo = ROOT / 'out/vendor-linux/unpacked/recovery_dtbo'
    if sha(dtb) != '740a196c7e9907d0bf0f13943355064548ab4cc08c9fa0519b8930ca9db837da' or sha(dtbo) != '852a13be138e8cea138c77f25446c9037246d608c2bb311f1402477b20d2abad':
        raise ValueError('Original DTB/DTBO references changed')
    result = {
        'status': args.policy + '-image-build-pass', 'image_path': str(image_path),
        'image_sha256': sha(image_path), 'image_bytes': len(image),
        'kernel_release': release, 'config_sha256': hashlib.sha256(candidate).hexdigest(),
        'config_changes': changes, 'embedded_config_matches_reviewed': True,
        'original_source_unchanged': True, 'original_dtb_dtbo_unchanged': True,
        'source_patch_sha256': '955cd6f6b991b1545153c4f01f204c484e68592dc3cb9506d40be90cf283b334',
        'all_original_export_crcs_preserved': not changed,
        'all_loaded_modules_rebuilt_and_imports_match': True,
        'loaded_modules_snapshot_sha256': sha(module_snapshot),
        'snapshot_boot_id': snapshot['boot_id'],
        'rebuilt_modules': [{'name': 'tabs7_hci_uart', 'path': str(module_path),
                             'sha256': sha(module_path), 'imports_checked': len(imports)}],
        'image_memory_delta_bytes': new_header[1] - old_header[1],
        'hardware_boot_verified': False, 'device_write_performed': False,
    }
    (output / 'kernel-verification.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
