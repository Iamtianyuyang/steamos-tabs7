#!/usr/bin/env python3
"""Offline kernel-only update of a verified working SM-T870 Linux Recovery.

Preserves the running initramfs, DTB, Recovery DTBO table, boot header and
command line. Requires independent build/ABI evidence and a live partition
backup. This script never accesses a device or writes a partition.

`--target boot` packs the same initramfs, DTB and command line as a BOOT
image instead (no embedded DTBO; the bootloader then applies the DTBO
partition, which must hold the identical official Lineage table).
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def config(image):
    start = image.index(b"IKCFG_ST") + 8
    end = image.index(b"IKCFG_ED", start)
    return gzip.decompress(image[start:end])


def checked(path, expected):
    data = path.read_bytes()
    if digest(data) != expected:
        raise ValueError(f"SHA-256 mismatch: {path}")
    return data


def config_changes(original, candidate):
    def values(data):
        result = {}
        for line in data.decode().splitlines():
            if line.startswith('CONFIG_'):
                key, value = line.split('=', 1)
                result[key] = value
            elif match := re.fullmatch(r'# (CONFIG_\w+) is not set', line):
                result[match[1]] = 'n'
        return result
    before, after = values(original), values(candidate)
    return {key: [before.get(key), after.get(key)]
            for key in sorted(before.keys() | after.keys())
            if before.get(key) != after.get(key)}


STEAMOS_IPC_CHANGES = {
    'CONFIG_SYSVIPC': ['n', 'y'],
    'CONFIG_SYSVIPC_COMPAT': [None, 'y'],
    'CONFIG_SYSVIPC_SYSCTL': [None, 'y'],
    'CONFIG_IPC_NS': [None, 'n'],
}
# systemd PID 1 (DEVTMPFS, FHANDLE), its pids controller and automount, and
# the user/PID namespaces of the Steam Linux Runtime container.
STEAMOS_SYSTEMD_CHANGES = {
    **STEAMOS_IPC_CHANGES,
    'CONFIG_AUTOFS_FS': ['n', 'y'],
    'CONFIG_CGROUP_PIDS': ['n', 'y'],
    'CONFIG_DEVTMPFS': ['n', 'y'],
    'CONFIG_DEVTMPFS_MOUNT': [None, 'n'],
    'CONFIG_FHANDLE': ['n', 'y'],
    'CONFIG_PID_NS': ['n', 'y'],
    'CONFIG_USER_NS': ['n', 'y'],
}
POLICIES = {'original': {}, 'steamos-ipc': STEAMOS_IPC_CHANGES,
            'steamos-systemd': STEAMOS_SYSTEMD_CHANGES}
ORIGINAL_CONFIG = ('out/vendor-linux/kernel.config',
                   '02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117')
# Official Lineage 20261011 boot.img: header reference for the BOOT target.
LINEAGE_BOOT = ('out/recovery-reference/lineage-20261011-extra/boot.img',
                'f34f7c7bafcfca4abe9132e218e9767002099735f8752fd8b7bd96e026a02154')


def reviewed_config(original, candidate, policy):
    if policy not in POLICIES:
        raise ValueError('Unknown configuration policy: ' + policy)
    changes = config_changes(original, candidate)
    expected = POLICIES[policy]
    if changes != expected or (policy == 'original' and original != candidate):
        raise ValueError(f'Configuration outside the {policy} policy: {changes}')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--build-report-sha256", required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument('--config-policy', choices=tuple(POLICIES),
                        default='original', help='only the explicit reviewed SteamOS deltas are additionally supported')
    parser.add_argument('--target', choices=('recovery', 'boot'), default='recovery')
    args = parser.parse_args()
    report_bytes = checked(args.build_report, args.build_report_sha256)
    report = json.loads(report_bytes)
    expected_status = ('matching-original-config-image-build-pass' if args.config_policy == 'original'
                       else args.config_policy + '-image-build-pass')
    if report["status"] != expected_status:
        raise ValueError("Kernel build did not pass independent verification")
    embedded_check = ('embedded_config_matches_original' if args.config_policy == 'original'
                      else 'embedded_config_matches_reviewed')
    abi_checks = (('all_original_export_crcs_preserved', 'h4_module_all_import_crcs_match')
                  if args.config_policy == 'original'
                  else ('all_loaded_modules_rebuilt_and_imports_match',))
    for name in (embedded_check, "original_source_unchanged",
                 "original_dtb_dtbo_unchanged", *abi_checks):
        if report.get(name) is not True:
            raise ValueError(f"Required build check failed: {name}")
    if args.config_policy == 'original' and report["config_changes"]:
        raise ValueError("Kernel configuration changes require a separate review")
    backup = args.backup.resolve()
    evidence = json.loads((backup / "manifest.json").read_text())
    if args.config_policy != 'original':
        if report['snapshot_boot_id'] != evidence['boot_id']:
            raise ValueError('Module review and partition backup come from different boots')
        checked(backup / 'modules.txt', report['loaded_modules_snapshot_sha256'])
        if report['loaded_modules_snapshot_sha256'] != evidence['modules.txt']['sha256']:
            raise ValueError('Loaded module review does not match backup manifest')
        for module in report['rebuilt_modules']:
            checked(Path(module['path']), module['sha256'])
    reservations = json.loads((backup / "live-reservations.json").read_text())
    factory = json.loads((ROOT / "hardware/samsung/gts7lwifi/profiles/gts7lwifi-T870XXS8DXH1-rev7.json").read_text())
    profile = json.loads((ROOT / "hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json").read_text())
    if (reservations["boot_id"] != evidence["boot_id"]
            or reservations["board_id"] != [8, 7]
            or reservations["msm_id"] != [356, 131073]
            or reservations["dtbo_index"] != 6
            or not reservations["lineage_profile_reservations_covered"]):
        raise ValueError("Live hardware/reservation evidence does not match")
    checked(backup / "live.dtb", reservations["fdt_sha256"])
    for name in ("boot", "dtbo", "recovery", "vbmeta"):
        entry = evidence["partitions"][name]
        data = checked(backup / (name + ".img"), entry["sha256"])
        if len(data) != profile["device_pit"]["partitions"][name.upper()]["capacity_bytes"]:
            raise ValueError(f"Incomplete original partition backup: {name}")
        if name in ("boot", "dtbo") and digest(data) != factory["original_images"][name + ".img"]["sha256"]:
            raise ValueError(f"Original factory partition changed: {name}")
    original = (backup / "recovery.img").read_bytes()
    image_path = Path(report["image_path"])
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    kernel = checked(image_path, report["image_sha256"])
    if len(kernel) != report["image_bytes"]:
        raise ValueError("New Image size disagrees with build evidence")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("vendor_builder", ROOT / "scripts/build/vendor/build-vendor-linux.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    mktools = ROOT / "out/tools/mkbootimg"
    options = builder.unpack(mktools, backup / "recovery.img", out / "original")
    if options["--header_version"] != "2" or int(options["--pagesize"], 0) != 4096:
        raise ValueError("Unsupported working Recovery layout")
    old_kernel = (out / "original/kernel").read_bytes()
    # Policies are deltas from the original vendor config, not from whichever
    # reviewed kernel the working image currently carries.
    baseline = checked(ROOT / ORIGINAL_CONFIG[0], ORIGINAL_CONFIG[1])
    if config_changes(baseline, config(old_kernel)) not in POLICIES.values():
        raise ValueError("Working image kernel is outside every reviewed configuration policy")
    changes = reviewed_config(baseline, config(kernel), args.config_policy)
    if digest(config(kernel)) != report["config_sha256"]:
        raise ValueError("New kernel embedded configuration differs from reviewed build")
    if args.config_policy != 'original' and changes != report['config_changes']:
        raise ValueError('Build report configuration delta differs from actual kernels')
    old_header = struct.unpack_from("<QQQ", old_kernel, 8)
    new_header = struct.unpack_from("<QQQ", kernel, 8)
    if (kernel[56:60] != b"ARM\x64" or new_header[0] != old_header[0]
            or new_header[0] != 0x80000 or new_header[2] != old_header[2]):
        raise ValueError("ARM64 Image entry layout changed")
    # The same configuration/DTB retains the loader's relocation policy. Check
    # any larger image also fits the running kernel's contiguous RAM window.
    iomem_data = checked(backup / "iomem.txt", evidence["iomem.txt"]["sha256"])
    if len(iomem_data) != evidence["iomem.txt"]["size"]:
        raise ValueError("IOMEM evidence size disagrees with the same-boot backup")
    iomem = iomem_data.decode()
    code = next(line.strip().split()[0] for line in iomem.splitlines() if " : Kernel code" in line)
    code_address = int(code.split("-")[0], 16)
    new_end = code_address + new_header[1]
    if new_header[1] < len(kernel) or new_end > 0xb0100000:
        raise ValueError("New kernel footprint exceeds the checked RAM window")
    for region in reservations["ranges"]:
        if code_address < region["address"] + region["size"] and region["address"] < new_end:
            raise ValueError(f"Kernel footprint overlaps {region['node']}")
    kernel_path = out / "Image"
    kernel_path.write_bytes(kernel)
    original_options = dict(options)
    options["--kernel"] = str(kernel_path)
    partition = args.target
    capacity = profile["device_pit"]["partitions"][partition.upper()]["capacity_bytes"]
    if args.target == 'boot':
        # Same header as the official Lineage boot.img except our command line.
        reference = checked(ROOT / LINEAGE_BOOT[0], LINEAGE_BOOT[1])
        if len(reference) != capacity or reference[-64:-60] != b"AVBf":
            raise ValueError("Unexpected official Lineage boot.img capacity/footer")
        boot_options = builder.unpack(mktools, ROOT / LINEAGE_BOOT[0], out / "lineage-boot")
        for name, value in boot_options.items():
            if name not in {"--kernel", "--ramdisk", "--dtb", "--cmdline"} and original_options.get(name) != value:
                raise ValueError(f"Working Recovery header differs from Lineage boot.img: {name}")
        if not original_options["--cmdline"].startswith(boot_options["--cmdline"] + " "):
            raise ValueError("Working command line no longer extends the Lineage boot command line")
        if (out / "lineage-boot/dtb").read_bytes() != (out / "original/dtb").read_bytes():
            raise ValueError("Lineage boot.img and working Recovery DTB differ")
        del options["--recovery_dtbo"], original_options["--recovery_dtbo"]
    draft = out / f"{partition}-payload.img"
    subprocess.run([sys.executable, str(mktools / "mkbootimg.py"),
                    *(part for pair in options.items() for part in pair), "--output", str(draft)], check=True)
    rebuilt = builder.unpack(mktools, draft, out / "rebuilt")
    path_options = {"--kernel", "--ramdisk", "--dtb", "--recovery_dtbo"}
    for name, value in original_options.items():
        if name not in path_options and rebuilt.get(name) != value:
            raise ValueError(f"Working boot header/command line changed: {name}")
    components = {}
    names = ("kernel", "ramdisk", "dtb") + (("recovery_dtbo",) if args.target == "recovery" else ())
    if args.target == "boot" and (out / "rebuilt/recovery_dtbo").exists():
        raise ValueError("BOOT image must not embed a DTBO")
    for name in names:
        data = (out / "rebuilt" / name).read_bytes()
        expected = kernel if name == "kernel" else (out / "original" / name).read_bytes()
        if data != expected:
            raise ValueError(f"Repacked component changed: {name}")
        components[name] = {"bytes": len(data), "sha256": digest(data), "unchanged": name != "kernel"}
    avb = [sys.executable, str(ROOT / "out/tools/avb/avbtool.py")]
    key = ROOT / "out/tools/avb/testkey_rsa4096.pem"
    subprocess.run(avb + ["verify_image", "--image", str(backup / "recovery.img"), "--key", str(key)], check=True)
    if original[-64:-60] != b"AVBf":
        raise ValueError("Original Recovery AVB footer missing")
    vm_offset = struct.unpack_from(">Q", original, len(original) - 44)[0]
    if original[vm_offset:vm_offset + 4] != b"AVB0":
        raise ValueError("Original Recovery AVB metadata missing")
    algorithm = struct.unpack_from(">I", original, vm_offset + 28)[0]
    rollback = struct.unpack_from(">Q", original, vm_offset + 112)[0]
    flags, location = struct.unpack_from(">II", original, vm_offset + 120)
    if algorithm != 2 or flags:
        raise ValueError("Unexpected Recovery AVB algorithm/flags")
    signed = out / f"{partition}.img"
    signed.write_bytes(draft.read_bytes())
    subprocess.run(avb + ["add_hash_footer", "--image", str(signed), "--partition_size", str(capacity),
                         "--partition_name", partition, "--algorithm", "SHA256_RSA4096", "--key", str(key),
                         "--rollback_index", str(rollback), "--rollback_index_location", str(location),
                         "--salt", digest(b"tabs7linux restored HCI socket"),
                         "--prop", "com.android.build.recovery.fingerprint:samsung/tabs7linux_gts7lwifi/gts7lwifi:linux/4.19/development:userdebug/test-keys",
                         "--prop", "com.tabs7linux.recovery.kind:native-vendor-linux-control"], check=True)
    subprocess.run(avb + ["verify_image", "--image", str(signed), "--key", str(key)], check=True)
    final = signed.read_bytes()
    payload = draft.read_bytes()
    if len(final) != capacity or final[:len(payload)] != payload:
        raise ValueError(f"Final {partition} capacity/payload changed")
    rollback_name = "recovery" if args.target == "recovery" else "boot"
    manifest = {"kind": "kernel-only update", "target_partition": partition.upper(),
                "config_policy": args.config_policy,
                "config_changes": changes, "image_sha256": digest(final),
                "image_bytes": len(final), "rollback_image": str(backup / (rollback_name + ".img")),
                "rollback_sha256": digest((backup / (rollback_name + ".img")).read_bytes()), "build_report_sha256": digest(report_bytes),
                "components": components, "header_and_commandline_preserved": True,
                "rollback_index": rollback, "rollback_index_location": location,
                "source_patch_sha256": report["source_patch_sha256"], "kernel_release": report["kernel_release"],
                "reserved_memory_delta_bytes": new_header[1] - old_header[1],
                "native_boot_observed": False, "device_write_performed": False}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified kernel-only {partition.upper()}: {signed}; SHA-256 {digest(final)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError) as error:
        sys.exit(f"Kernel repack failed: {error}")
