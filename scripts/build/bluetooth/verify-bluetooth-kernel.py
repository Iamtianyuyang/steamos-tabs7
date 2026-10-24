#!/usr/bin/env python3
"""Verify the isolated HCI socket repair before a parent considers packaging it."""
import argparse
import gzip
import hashlib
import json
import re
import shutil
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "out/bluetooth/repro-kernel"
BUILD = HERE / "build"


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def header(data):
    assert data[56:60] == b"ARM\x64", "Missing ARM64 Image magic"
    offset, size, flags = struct.unpack_from("<QQQ", data, 8)
    return {"text_offset": hex(offset), "image_size": size, "flags": hex(flags)}


def symvers(path):
    symbols = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 4:
            crc, name, owner, export = fields[:4]
            assert name not in symbols, "Duplicate export " + name
            symbols[name] = {"crc": int(crc, 16), "owner": owner, "export": export}
    return symbols


def verify():
    assert (HERE / "build.status").read_text().strip() == "0", "Build incomplete or failed"
    config = (ROOT / "out/vendor-linux/kernel.config").read_bytes()
    assert (BUILD / ".config").read_bytes() == config, "Config byte mismatch"
    source = json.loads((HERE / "source-manifest.json").read_text())
    assert sha(HERE / "src/net/bluetooth/hci_sock.c") == source["candidate_source_sha256"]
    assert sha(ROOT / source["original_source_path"]) == source["original_source_sha256"]
    image_path = BUILD / "arch/arm64/boot/Image"
    image = image_path.read_bytes()
    original_path = ROOT / "out/vendor-linux/reference/kernel"
    original = original_path.read_bytes()
    old_header, new_header = header(original), header(image)
    assert old_header["text_offset"] == new_header["text_offset"]
    assert old_header["flags"] == new_header["flags"]
    marker = image.index(b"IKCFG_ST") + 8
    end = image.index(b"IKCFG_ED", marker)
    assert gzip.decompress(image[marker:end]) == config, "Embedded config differs"
    release = (BUILD / "include/config/kernel.release").read_text().strip()
    assert release == "4.19.325-cip136-st20-perf-g4478511c4b37", "Kernel release differs"
    version = re.search(rb"Linux version " + re.escape(release.encode()) + rb" [^\x00\n]+", image)
    assert version and version.group().startswith(b"Linux version " + release.encode() + b" ")
    names = {line.split()[2] for line in (BUILD / "System.map").read_text().splitlines() if len(line.split()) == 3}
    required = ["__cfi_check", "__cfi_slowpath", "hci_sock_create", "hci_sock_bind", "hci_sock_release", "hci_sock_ops", "hci_sock_destruct", "hci_sk_list", "sock_cookie_ida"]
    found = {key: sorted(name for name in names if name == key or name.startswith(key + ".")) for key in required}
    assert all(found.values()), "Restored socket or CFI symbol absent"
    trusted = symvers(ROOT / "out/references/recovered-module-abi/Module.symvers")
    rebuilt = symvers(BUILD / "Module.symvers")
    missing = sorted(trusted.keys() - rebuilt.keys())
    changed = [{"name": name, "original": trusted[name], "rebuilt": rebuilt[name]} for name in trusted.keys() & rebuilt.keys() if trusted[name]["crc"] != rebuilt[name]["crc"] or trusted[name]["export"] != rebuilt[name]["export"]]
    abi = {"original_export_count": len(trusted), "rebuilt_export_count": len(rebuilt), "missing_original_exports": missing, "changed_original_exports": changed, "added_exports": sorted(rebuilt.keys() - trusted.keys())}
    (HERE / "export-abi-verification.json").write_text(json.dumps(abi, indent=2) + "\n")
    assert not missing and not changed, "Original exported ABI differs; inspect export-abi-verification.json"
    h4 = json.loads((ROOT / "hardware/samsung/gts7lwifi/profiles/bluetooth-h4.json").read_text())
    assert all(item["name"] in rebuilt and rebuilt[item["name"]]["crc"] == int(item["crc"], 16) for item in h4["imports"]), "H4 import CRC differs"
    references = json.loads((HERE / "immutable-reference-inputs.json").read_text())
    assert all(sha(ROOT / item["path"]) == item["sha256"] for item in references), "Reference input changed"
    digest = sha(image_path)
    artifacts = HERE / "artifacts"
    artifacts.mkdir(exist_ok=True)
    immutable = artifacts / ("Image-hci-restored-" + digest + ".bin")
    if immutable.exists():
        assert sha(immutable) == digest
    else:
        shutil.copyfile(image_path, immutable)
        immutable.chmod(0o444)
    result = {"status": "matching-original-config-image-build-pass", "device_boot_tested": False, "bluetooth_controller_tested": False, "image_path": str(immutable.relative_to(ROOT)), "image_sha256": digest, "image_bytes": len(image), "original_image_bytes": len(original), "stored_image_delta_bytes": len(image) - len(original), "arm64_header": new_header, "original_arm64_header": old_header, "reserved_image_memory_delta_bytes": new_header["image_size"] - old_header["image_size"], "kernel_release": release, "kernel_version_banner": version.group().decode(), "config_sha256": hashlib.sha256(config).hexdigest(), "config_changes": [], "embedded_config_matches_original": True, "source_patch_sha256": source["patch_sha256"], "original_source_unchanged": True, "original_dtb_dtbo_unchanged": True, "cfi_and_restored_symbols": found, "all_original_export_crcs_preserved": True, "h4_module_all_import_crcs_match": True, "h4_module_sha256": h4["module_sha256"], "symvers_sha256": sha(BUILD / "Module.symvers"), "vmlinux_sha256": sha(BUILD / "vmlinux")}
    (HERE / "kernel-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-root", type=Path, default=HERE)
    args = parser.parse_args()
    HERE = args.build_root.resolve()
    BUILD = HERE / "build"
    verify()
