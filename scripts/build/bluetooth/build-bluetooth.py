#!/usr/bin/env python3
"""Reproduce the vendor HCI socket repair and/or matching external H4 module.

Uses the already verified vendor preparation under ignored out/. No source
download, host package installation, device command, or flash is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
CONFIG_SHA = "02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117"
SOURCE_SHA = "1dfdbc3320ff9001b513e136dfe665f590bcb809861b578b8bc131f5dbb9616d"
PATCH_SHA = "955cd6f6b991b1545153c4f01f204c484e68592dc3cb9506d40be90cf283b334"
FIXED_SHA = "3fdb66d9e07421e5f7b61d35ce0f2729358d2dfeaf3bdd25c8311bfff45f2df8"
IMAGE = "sha256:9b873c5a32d15f40254663d533264c9c64082b4f19331e70a337fd386acac174"
PINNED_COMMIT = "4478511c4b3738ad117e674810c2dfde154724bf"
H4_SHA = "c70a7c1105789be7f3679ec149108312d94d016dd2ff5d298cb8e8e0e9a5aef8"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def logged(command, output, name):
    with (output / (name + ".log")).open("w") as stream:
        result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=False)
    (output / (name + ".status")).write_text(str(result.returncode) + "\n")
    if result.returncode:
        raise RuntimeError("Build failed; see " + str(output / (name + ".log")))


def docker(prep, output, source, build, module=None):
    command = ["docker", "run", "--rm", "--network", "none",
               "-v", str(prep) + ":/work:ro",
               "-v", str(source) + ":/work/src:ro",
               "-v", str(build) + (":/work/build-abi:rw" if module else ":/work/build:rw"),
               "-v", str(output) + ":/result:rw"]
    if module:
        command += ["-v", str(module) + ":/bt/module:rw"]
    return command + [IMAGE, "sh", "/result/run-build.sh"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("kernel", "h4"), required=True)
    parser.add_argument("--prep", type=Path, default=ROOT / "out/vendor-kernel-prep")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, help="default: 8 for kernel, 2 for H4")
    parser.add_argument("--dry-run", action="store_true", help="validate preparation and print the isolated build plan")
    args = parser.parse_args()
    if args.jobs is None:
        args.jobs = 8 if args.phase == "kernel" else 2
    prep = args.prep.resolve()
    output = (args.output or ROOT / ("out/bluetooth/repro-" + args.phase)).resolve()
    config = ROOT / "out/vendor-linux/kernel.config"
    patch = ROOT / "hardware/samsung/gts7lwifi/kernel/patches/vendor-4.19/hci-socket-restore.patch"
    if not 1 <= args.jobs <= 32:
        raise RuntimeError("--jobs must be between 1 and 32")
    if digest(config) != CONFIG_SHA or digest(patch) != PATCH_SHA:
        raise RuntimeError("Original config or reviewed source patch mismatch")
    if digest(prep / "src/net/bluetooth/hci_sock.c") != SOURCE_SHA:
        raise RuntimeError("Prepared source differs from the official pinned source")
    inputs = json.loads((prep / "build-inputs.json").read_text())
    if inputs["kernel_commit"] != PINNED_COMMIT or inputs["container_image"] != IMAGE or inputs["toolchain"] != "clang-r563880c" or inputs["toolchain_build_id"] != "14054515":
        raise RuntimeError("Pinned source/toolchain/container preparation mismatch")
    if inputs["kernel_archive_sha256"] != "9a5cf350170df5be0e09df44897a1778c5654318575bfff30148c6dfa2e5254f" or inputs["toolchain_archive_sha256"] != "c88af895c47d1a5e442cb663cdd539efae866208a5acedad58786907bacc2fa3":
        raise RuntimeError("Preparation archive digest mismatch")
    plan = {"phase": args.phase, "source_commit": PINNED_COMMIT, "config_sha256": CONFIG_SHA,
            "source_patch_sha256": PATCH_SHA, "container_image": IMAGE,
            "toolchain": "AOSP clang-r563880c (Android 14054515)",
            "output": str(output), "jobs": args.jobs,
            "original_source_writable": False, "device_operations": False}
    print(json.dumps(plan, indent=2), flush=True)
    if args.dry_run:
        return
    if output.exists():
        raise RuntimeError("Output already exists; choose a separate --output to preserve prior artifacts")
    output.mkdir(parents=True)
    (output / "inputs.json").write_text(json.dumps(plan, indent=2) + "\n")
    source = prep / "src"
    if args.phase == "kernel":
        source = output / "src"
        shutil.copytree(prep / "src", source, copy_function=os.link, symlinks=True)
        # Detach this one inode before GNU patch touches it. All other source
        # files remain shared read-only inodes and are mounted read-only.
        target = source / "net/bluetooth/hci_sock.c"
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            stream.write(target.read_bytes())
            temporary = Path(stream.name)
        os.replace(temporary, target)
        target.chmod(0o644)
        subprocess.run(["patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=source, check=True)
        if digest(target) != FIXED_SHA or digest(prep / "src/net/bluetooth/hci_sock.c") != SOURCE_SHA:
            raise RuntimeError("Isolated source patch or original-source invariance failed")
        (output / "source-manifest.json").write_text(json.dumps({
            "original_source_path": str(prep / "src/net/bluetooth/hci_sock.c"),
            "original_source_sha256": SOURCE_SHA, "candidate_source_sha256": FIXED_SHA,
            "patch_sha256": PATCH_SHA}, indent=2) + "\n")
        references = []
        for relative in ("out/vendor-linux/reference/kernel", "out/vendor-linux/reference/dtb",
                         "out/vendor-linux/unpacked/dtb", "out/vendor-linux/unpacked/recovery_dtbo",
                         "out/vendor-linux/kernel.config"):
            path = ROOT / relative
            references.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)})
        (output / "immutable-reference-inputs.json").write_text(json.dumps(references, indent=2) + "\n")
        subprocess.run(["cp", "-a", "--reflink=auto", str(prep / "build-debugfs"), str(output / "build")], check=True)
        build = output / "build"
    else:
        build = output / "build"
        shutil.copytree(prep / "build-abi", build, symlinks=True)
        module = output / "h4"
        module.mkdir()
        for name in ("hci_ldisc.c", "hci_h4.c", "hci_uart.h"):
            shutil.copy2(source / "drivers/bluetooth" / name, module / name)
        (module / "Makefile").write_text("obj-m += tabs7_hci_uart.o\ntabs7_hci_uart-y := hci_ldisc.o hci_h4.o\nccflags-y += -I$(srctree)/drivers/bluetooth -DCONFIG_BT_HCIUART_H4\n")
        shutil.copyfile(ROOT / "out/references/recovered-module-abi/Module.symvers", build / "Module.symvers")
    shutil.copyfile(config, build / ".config")
    make = "make -C /work/src O=" + ("/work/build" if args.phase == "kernel" else "/work/build-abi") + " ARCH=arm64 LLVM=1 LLVM_IAS=1 LD=\"ld.lld --threads=" + str(args.jobs) + "\""
    script = "#!/bin/sh\nset -eu\nexport PATH=/work/toolchain/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\nexport LC_ALL=C\n"
    if args.phase == "kernel":
        script += "export KBUILD_BUILD_USER=tabs7linux\nexport KBUILD_BUILD_HOST=isolated-builder\nexport KBUILD_BUILD_VERSION=1\nexport KBUILD_BUILD_TIMESTAMP=\"Tue Oct 6 06:28:02 UTC 2026\"\n"
        script += make + " olddefconfig\n"
        script += "test \"$(sha256sum /work/build/.config | cut -d' ' -f1)\" = " + CONFIG_SHA + "\n"
        script += make + " -j" + str(args.jobs) + " Image\n"
    else:
        script += make + " M=/bt/module -j" + str(args.jobs) + " modules\n"
    (output / "run-build.sh").write_text(script)
    logged(docker(prep, output, source, build, output / "h4" if args.phase == "h4" else None), output, "build")
    artifact = build / "arch/arm64/boot/Image" if args.phase == "kernel" else output / "h4/tabs7_hci_uart.ko"
    report = {**plan, "build_status": 0, "artifact": str(artifact), "artifact_sha256": digest(artifact),
              "config_after_sha256": digest(build / ".config"),
              "abi_and_embedded_config_verified": False, "device_tested": False}
    (output / "build-result.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["config_after_sha256"] != CONFIG_SHA:
        raise RuntimeError("Build changed original config")
    if args.phase == "h4":
        report["identical_to_hardware_verified_h4_artifact"] = report["artifact_sha256"] == H4_SHA
        (output / "build-result.json").write_text(json.dumps(report, indent=2) + "\n")
        if not report["identical_to_hardware_verified_h4_artifact"]:
            raise RuntimeError("H4 bytes differ from the hardware-verified artifact; do not auto-deploy")
    else:
        subprocess.run([shutil.which("python3"), str(ROOT / "scripts/build/bluetooth/verify-bluetooth-kernel.py"),
                        "--build-root", str(output)], check=True)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
