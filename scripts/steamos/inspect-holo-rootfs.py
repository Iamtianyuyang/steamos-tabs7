#!/usr/bin/env python3
"""Read-only inventory of an assembled SteamOS aarch64 rootfs image.

The host builds run without root. ext4 uses read-only debugfs; Btrfs uses a
filtered btrfs restore into a temporary inspection tree. The image is unchanged.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# Paths that decide whether a session can start at all.
PROBE_PATHS = (
    "/usr/bin/steam",
    "/usr/bin/gamescope",
    "/usr/bin/gamescopereaper",
    "/usr/lib/steamos/gamescope-session",
    "/usr/lib/steamos/steamos-session-select",
    "/usr/lib/steam",
    "/usr/lib/systemd/systemd",
    "/usr/bin/bash",
    "/usr/bin/plasmashell",
    "/usr/bin/startplasma-wayland",
    "/usr/bin/fex",
    "/usr/bin/FEX",
    "/usr/bin/FEXInterpreter",
    "/usr/share/vulkan/icd.d/freedreno_icd.aarch64.json",
    "/usr/lib/libvulkan_freedreno.so",
    "/usr/share/steam/compatibilitytools.d",
    "/var/lib/overlays/etc/upper",
)

LIST_DIRS = (
    "/usr/lib/steamos",
    "/usr/share/vulkan/icd.d",
    "/home",
    "/var/lib/overlays",
)


class Image:
    def __init__(self, path: Path) -> None:
        if not shutil.which("debugfs"):
            raise SystemExit("debugfs is required (e2fsprogs)")
        self.path = path
        with path.open("rb") as handle:
            handle.seek(0x438)
            if handle.read(2) != b"\x53\xef":
                raise ValueError("debugfs requires an ext4 image")

    def run(self, command: str, allow_failure: bool = True) -> str:
        result = subprocess.run(
            ["debugfs", "-R", command, str(self.path)],
            text=True,
            capture_output=True,
        )
        if result.returncode != 0 and not allow_failure:
            raise SystemExit(f"debugfs {command!r} failed: {result.stderr.strip()}")
        return result.stdout

    def cat(self, path: str) -> str:
        return self.run(f"cat {path}")

    def stat(self, path: str) -> str:
        return self.run(f"stat {path}")

    def ls(self, path: str) -> list[str]:
        out = self.run(f"ls -p {path}")
        names = [line.split("/")[5] for line in out.splitlines()
                 if line.startswith("/") and len(line.split("/")) > 5]
        return sorted(str(Path(path) / name) for name in names if name not in (".", ".."))

    def dump(self, path: str, target: Path) -> bool:
        target.unlink(missing_ok=True)
        self.run(f"dump {path} {target}")
        return target.is_file()


def filesystem_type(path: Path) -> str:
    with path.open("rb") as handle:
        handle.seek(0x10040)
        if handle.read(8) == b"_BHRfS_M":
            return "btrfs"
        handle.seek(0x438)
        if handle.read(2) == b"\x53\xef":
            return "ext4"
    raise ValueError("unsupported filesystem: expected Btrfs or ext4")


class BtrfsImage:
    """Read an inspection subset; it is not a deployable rootfs extraction."""
    def __init__(self, path: Path, tool: str, temporary: Path) -> None:
        executable = shutil.which(tool)
        if not executable:
            raise ValueError("btrfs-progs is required; use --btrfs-tool /path/to/btrfs")
        self.root = temporary / "tree"
        self.root.mkdir()
        paths = set()
        for name in (*PROBE_PATHS, *LIST_DIRS, "/usr/lib/os-release", "/etc/passwd",
                     "/usr/lib/libc.so.6", "/var/lib/pacman/local"):
            item = Path(name)
            paths.add(str(item))
            paths.update(str(parent) for parent in item.parents)
        terms = [re.escape(name) for name in sorted(paths)]
        terms.extend(re.escape(name) + r"/[^/]+" for name in LIST_DIRS)
        terms.append(r"/var/lib/pacman/local/[^/]+(/desc)?")
        subprocess.run([executable, "restore", "--path-regex", "^(" + "|".join(terms) + ")$",
                        str(path.resolve()), str(self.root)], check=True,
                       capture_output=True, text=True)
        # No -S: image symlinks must never redirect inspection into the host.

    def local(self, name: str) -> Path:
        return self.root / name.lstrip("/")

    def cat(self, name: str) -> str:
        if name == "/etc/os-release":
            name = "/usr/lib/os-release"
        item = self.local(name)
        return item.read_text(errors="replace") if item.is_file() else ""

    def stat(self, name: str) -> str:
        item = self.local(name)
        if not item.exists():
            return ""
        # The restored subset does not preserve executable mode or ownership.
        return f"Inode: inspected Size: {item.stat().st_size}"

    def ls(self, name: str) -> list[str]:
        item = self.local(name)
        return sorted(str(Path(name) / child.name) for child in item.iterdir()) if item.is_dir() else []

    def dump(self, name: str, target: Path) -> bool:
        item = self.local(name)
        if not item.is_file():
            return False
        shutil.copyfile(item, target)
        return True


def glibc_version(libc: Path) -> str | None:
    strings = subprocess.run(
        ["strings", "-a", str(libc)], text=True, capture_output=True
    ).stdout
    match = re.search(r"GNU C Library[^\n]*release version ([0-9.]+)", strings)
    if not match:
        match = re.search(r"glibc ([0-9]+\.[0-9]+)", strings)
    return match.group(1).rstrip(".") if match else None


def inspect(image, temporary: Path) -> None:
    if not re.search(r"^ID=", image.cat("/etc/os-release"), re.MULTILINE):
        raise ValueError("no readable os-release: inspection did not succeed")

    print("== os-release")
    print(image.cat("/etc/os-release").strip())

    print("\n== users (uid >= 1000)")
    for line in image.cat("/etc/passwd").splitlines():
        fields = line.split(":")
        if len(fields) >= 7 and fields[2].isdigit() and int(fields[2]) >= 1000:
            print(f"  {fields[0]} uid={fields[2]} gid={fields[3]} home={fields[5]} shell={fields[6]}")

    print("\n== probed paths (restored regular files/directories; symlinks omitted)")
    for path in PROBE_PATHS:
        info = image.stat(path)
        size = re.search(r"Size: (\d+)", info)
        print(f"  {'present' if 'Inode:' in info else 'not inspected':13} {path} size={size.group(1) if size else '?'}")

    for directory in LIST_DIRS:
        print(f"\n== ls {directory}")
        entries = image.ls(directory)
        for entry in entries[:40]:
            print("  " + entry)
        if len(entries) > 40:
            print(f"  … {len(entries) - 40} more")

    print("\n== pacman packages")
    packages = []
    for entry in image.ls("/var/lib/pacman/local"):
        name = Path(entry.split()[-1]).name
        desc = image.cat(f"/var/lib/pacman/local/{name}/desc")
        fields = dict(re.findall(r"%([A-Z]+)%\n([^\n]+)", desc))
        if "NAME" in fields:
            packages.append(fields)
    print(f"  readable database entries: {len(packages)}")
    if not packages:
        print("  Package database unavailable; this does not mean no software is installed.")
    for fields in sorted(packages, key=lambda item: item["NAME"]):
        if re.match(r"^(steam|gamescope|mesa|fex|wine|proton|plasma|kde|pipewire|systemd|glibc|linux)", fields["NAME"], re.I):
            print(f"  {fields['NAME']} {fields.get('VERSION', '?')} {fields.get('ARCH', '?')}")

    print("\n== glibc")
    libc = temporary / "libc.so.6"
    if image.dump("/usr/lib/libc.so.6", libc) or image.dump("/usr/lib64/libc.so.6", libc):
        print(f"  {glibc_version(libc) or 'unknown'}")
    else:
        print("  libc.so.6 not inspected")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--btrfs-tool", default="btrfs")
    args = parser.parse_args()
    if not args.image.is_file():
        raise SystemExit(f"missing image: {args.image}")
    try:
        filesystem = filesystem_type(args.image)
        print(f"== filesystem: {filesystem} (read-only source; no target programs executed)")
        with tempfile.TemporaryDirectory(prefix="tabs7-steamos-inspect-") as directory:
            temporary = Path(directory)
            image = BtrfsImage(args.image, args.btrfs_tool, temporary) if filesystem == "btrfs" else Image(args.image)
            inspect(image, temporary)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"inspection failed: {error}\n")



if __name__ == "__main__":
    main()
