#!/usr/bin/env python3
"""Verify recovery bootstrap inputs; optionally read Android properties. Never flash."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def check_file(path, expected):
    size = path.stat().st_size
    if expected.get("size") is not None and size != expected["size"]:
        raise ValueError(f"{path}: size mismatch: {size}")
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != expected["sha256"]:
        raise ValueError(f"{path}: SHA-256 mismatch")
    return {"path": str(path.relative_to(ROOT)), "size": size, "sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", action="store_true", help="Read and verify Android ADB properties")
    parser.add_argument("--pit", action="store_true", help="Verify the previously captured Download-mode PIT")
    args = parser.parse_args()
    profile = json.loads((ROOT / "hardware/samsung/gts7lwifi/profiles/gts7lwifi-recovery-bootstrap.json").read_text())
    factory = json.loads((ROOT / "hardware/samsung/gts7lwifi/profiles/gts7lwifi-T870XXS8DXH1-rev7.json").read_text())
    report = {
        "writes_performed": False,
        "data_reset_authorization": "Not evaluated by this read-only preflight; consult current user instructions",
        "files": {},
    }
    for name, expected in profile["files"].items():
        report["files"][name] = check_file(ROOT / expected["path"], expected)
    for name in ("recovery.img", "vbmeta.img", "boot.img", "dtbo.img"):
        path = ROOT / "out/stock-firmware" / profile["firmware"] / name
        report["files"][f"factory-{name}"] = check_file(path, factory["original_images"][name])
    vbmeta = (ROOT / profile["files"]["vbmeta"]["path"]).read_bytes()
    if vbmeta[:4] != b"AVB0" or struct.unpack_from(">I", vbmeta, 120)[0] != 3:
        raise ValueError("Expected AVB0 vbmeta with flags=3")
    with (ROOT / profile["files"]["recovery"]["path"]).open("rb") as source:
        magic = source.read(8)
    if magic != b"ANDROID!":
        raise ValueError("Expected Android recovery boot image")
    if args.pit:
        pit = profile["device_pit"]
        report["files"]["device-pit"] = check_file(ROOT / pit["path"], pit)
        report["partitions"] = pit["partitions"]
        for name, entry in pit["partitions"].items():
            if entry["block_count"] * pit["partition_block_bytes"] != entry["capacity_bytes"]:
                raise ValueError(f"{name}: inconsistent PIT capacity")
        for name in ("recovery", "vbmeta"):
            if report["files"][name]["size"] > pit["partitions"][name.upper()]["capacity_bytes"]:
                raise ValueError(f"{name}: image exceeds partition capacity")
    if args.device:
        checks = {
            "ro.product.model": profile["model"],
            "ro.product.device": profile["device"],
            "ro.boot.bootloader": profile["firmware"],
            "ro.boot.revision": profile["hardware_revision"],
            "ro.boot.flash.locked": "0",
            "ro.boot.verifiedbootstate": "orange",
        }
        report["device"] = {}
        for key, expected in checks.items():
            value = subprocess.check_output(["adb", "-d", "shell", "getprop", key], timeout=10, text=True).strip()
            if value != expected:
                raise ValueError(f"{key}: expected {expected!r}, got {value!r}")
            report["device"][key] = value
        # After disabling AVB, this property can be absent. Still require both
        # flash.locked=0 and verifiedbootstate=orange independently above.
        state = subprocess.check_output(
            ["adb", "-d", "shell", "getprop", "ro.boot.vbmeta.device_state"], timeout=10, text=True
        ).strip()
        if state not in ("unlocked", ""):
            raise ValueError(f"Unexpected AVB device state: {state!r}")
        report["device"]["ro.boot.vbmeta.device_state"] = state
        report["device"]["ro.boot.veritymode"] = subprocess.check_output(
            ["adb", "-d", "shell", "getprop", "ro.boot.veritymode"], timeout=10, text=True
        ).strip()
        adb_state = subprocess.check_output(["adb", "-d", "get-state"], timeout=10, text=True).strip()
        allowed = subprocess.check_output(
            ["adb", "-d", "shell", "getprop", "sys.oem_unlock_allowed"], timeout=10, text=True
        ).strip()
        report["device"]["adb_state"] = adb_state
        report["device"]["sys.oem_unlock_allowed"] = allowed
        if adb_state not in ("device", "recovery"):
            raise ValueError(f"Unexpected ADB state: {adb_state!r}")
        # After Samsung's required reset, confirm Android still allows OEM
        # unlocking; flash.locked=0 alone does not verify the Android setting.
        if adb_state == "device" and allowed != "1":
            raise ValueError(f"Android OEM unlock is not confirmed enabled: {allowed!r}")
    report["remaining"] = [
        "Explicit user authorization for factory reset and loss of internal data",
        "Install official recovery, obtain root ADB, then capture actual partition images and live FDT",
        "Compare live reservations and validate AVB/DTBO handling before testing the Linux draft",
    ]
    if not args.pit:
        report["remaining"].insert(1, "Verify the saved Download-mode PIT with --pit before writing")
    destination = ROOT / "out/recovery-reference/preflight.json"
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        sys.exit(f"Preflight failed: {exc}")
