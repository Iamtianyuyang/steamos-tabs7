#!/usr/bin/env python3
"""Fetch the official SteamOS (Holo/Deckard) aarch64 rootfs image.

Valve's Steam Frame images are RAUC bundles whose payload is a casync index;
the file system itself is fetched as zstd-compressed, content-addressed
chunks.  The bundle is small (about 2 MiB) but the assembled image is 10 GiB
(sparse, mostly zero).  This script pins one build and verifies both the RAUC
manifest hash and the final image hash.

Everything is written under a caller-provided output directory; the repository
default is the ignored ``out/steamos/`` tree.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import zstandard

# Pinned Steam Frame image.  Change these together with the documented hash in
# docs/steamos/README.md; never accept a manifest that disagrees with them.
BUILD = "20261002.6232440"
VERSION = "0.5.3"
BUNDLE = f"deckard-{BUILD}-{VERSION}.raucb"
BASE_URL = f"https://steamdeck-images.steamos.cloud/vr/{BUILD}"
ROOTFS_SHA256 = "63950ce7f4654be7ad8b3060bbef63579a2758308ed14ed5d130d154ea98a8de"
ROOTFS_SIZE = 10 * 1024**3
# The per-build store holds the chunks that changed in this release; the
# shared store holds everything else.  Both are content addressed, so a chunk
# is accepted from whichever store serves it first.
STORES = (
    f"{BASE_URL}/{BUNDLE}.castr",
    "https://steamdeck-images.steamos.cloud/vr/chunks.castr",
)

CA_FORMAT_INDEX = 0x96824D9C7B129FF9
CA_FORMAT_TABLE = 0xE75B9E112F17417D
CA_FORMAT_TABLE_TAIL_MARKER = 0x4B4F050E5549ECD1
INDEX_HEADER_LEN = 48
TABLE_HEADER_LEN = 16
TABLE_ENTRY_LEN = 40


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def log(message: str) -> None:
    print(f"==> {message}", flush=True)


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def fetch_bundle(dest_dir: Path, refresh: bool) -> tuple[Path, Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    raucb = dest_dir / BUNDLE
    manifest = dest_dir / "bundle" / "manifest.raucm"
    caibx = dest_dir / "bundle" / "rootfs.img.caibx"
    if refresh or not manifest.is_file() or not caibx.is_file():
        if refresh or not raucb.is_file():
            log(f"downloading {BUNDLE}")
            tmp = raucb.with_suffix(".part")
            with requests.get(f"{BASE_URL}/{BUNDLE}", stream=True, timeout=60) as response:
                response.raise_for_status()
                with tmp.open("wb") as handle:
                    for block in response.iter_content(1024 * 256):
                        handle.write(block)
            os.replace(tmp, raucb)
        if not shutil_which("unsquashfs"):
            die("unsquashfs is required (install squashfs-tools)")
        log("extracting RAUC metadata")
        bundle_dir = dest_dir / "bundle"
        if bundle_dir.exists():
            subprocess.run(["rm", "-rf", str(bundle_dir)], check=True)
        subprocess.run(
            ["unsquashfs", "-q", "-d", str(bundle_dir), str(raucb)],
            check=True,
        )
    if not manifest.is_file() or not caibx.is_file():
        die("RAUC bundle is missing manifest.raucm or rootfs.img.caibx")
    return manifest, caibx


def shutil_which(name: str) -> str | None:
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def read_manifest(manifest: Path) -> tuple[str, int]:
    section = None
    sha256 = None
    size = None
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section != "image.rootfs" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "sha256":
            sha256 = value
        elif key == "size":
            size = int(value)
    if not sha256 or not size:
        die("manifest.raucm has no image.rootfs sha256/size")
    return sha256, size


def parse_caibx(path: Path) -> tuple[int, list[tuple[int, int, bytes]]]:
    data = path.read_bytes()
    size, magic, _flags, _min_sz, _avg_sz, max_sz = struct.unpack_from("<6Q", data, 0)
    if magic != CA_FORMAT_INDEX:
        die(f"not a casync index: magic={magic:#x}")
    if size != INDEX_HEADER_LEN:
        die(f"unexpected index header size {size}")
    _tsize, tmagic = struct.unpack_from("<2Q", data, INDEX_HEADER_LEN)
    if tmagic != CA_FORMAT_TABLE:
        die(f"missing casync table: magic={tmagic:#x}")

    body = data[INDEX_HEADER_LEN + TABLE_HEADER_LEN :]
    if len(body) % TABLE_ENTRY_LEN:
        die("truncated casync table")
    count = len(body) // TABLE_ENTRY_LEN - 1
    tail = body[count * TABLE_ENTRY_LEN :]
    if struct.unpack_from("<Q", tail, 32)[0] != CA_FORMAT_TABLE_TAIL_MARKER:
        die("bad casync table tail marker")

    chunks: list[tuple[int, int, bytes]] = []
    previous = 0
    for index in range(count):
        record = body[index * TABLE_ENTRY_LEN : (index + 1) * TABLE_ENTRY_LEN]
        offset = struct.unpack_from("<Q", record, 0)[0]
        length = offset - previous
        if length <= 0 or length > max_sz:
            die(f"bad chunk length {length} at {index}")
        chunks.append((previous, length, record[8:40]))
        previous = offset
    return previous, chunks


def chunk_path(cache: Path, digest: bytes) -> Path:
    hex_digest = digest.hex()
    return cache / hex_digest[:4] / f"{hex_digest}.cacnk"


def download_chunk(session: requests.Session, digest: bytes, destination: Path) -> None:
    hex_digest = digest.hex()
    errors: list[str] = []
    for store in STORES:
        url = f"{store.rstrip('/')}/{hex_digest[:4]}/{hex_digest}.cacnk"
        for attempt in range(5):
            try:
                response = session.get(url, timeout=30)
                if response.status_code == 404:
                    break
                response.raise_for_status()
                destination.parent.mkdir(parents=True, exist_ok=True)
                tmp = destination.with_suffix(".part")
                tmp.write_bytes(response.content)
                os.replace(tmp, destination)
                return
            except Exception as exc:  # noqa: BLE001 - reported below
                errors.append(f"{url}: {exc}")
                time.sleep(min(8.0, 0.4 * 2**attempt))
    die(f"cannot fetch chunk {hex_digest}\n" + "\n".join(errors[-3:]))


def assemble(caibx: Path, output: Path, cache: Path, workers: int, offline: bool) -> None:
    image_size, chunks = parse_caibx(caibx)
    if image_size != ROOTFS_SIZE:
        die(f"index describes {image_size} bytes, expected {ROOTFS_SIZE}")
    unique: dict[bytes, list[tuple[int, int]]] = defaultdict(list)
    for offset, length, digest in chunks:
        unique[digest].append((offset, length))
    payload = sum(length for (_, length, _) in {c[2]: c for c in chunks}.values())
    log(
        f"image {image_size / 1024**3:.1f} GiB, {len(chunks)} chunk refs, "
        f"{len(unique)} unique, {payload / 1024**3:.2f} GiB of unique payload"
    )
    if offline:
        return

    cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_TRUNC)
    state = {"fetched": 0, "reused": 0, "placed": 0, "zero": 0}
    lock = threading.Lock()
    started = time.time()
    try:
        os.ftruncate(fd, image_size)

        def work(digest: bytes, locations: list[tuple[int, int]]) -> None:
            thread = threading.current_thread()
            decompressor = getattr(thread, "dctx", None)
            if decompressor is None:
                decompressor = zstandard.ZstdDecompressor()
                thread.dctx = decompressor
            session = getattr(thread, "http", None)
            if session is None:
                session = requests.Session()
                session.headers["User-Agent"] = "tabs7linux-steamos-rootfs/1"
                thread.http = session

            path = chunk_path(cache, digest)
            reused = path.is_file()
            if not reused:
                download_chunk(session, digest, path)
            blob = path.read_bytes()
            expected = locations[0][1]
            try:
                raw = decompressor.decompress(blob, max_output_size=expected)
            except zstandard.ZstdError as exc:
                raise SystemExit(f"chunk {digest.hex()} is not zstd: {exc}")
            if len(raw) != expected:
                raise SystemExit(
                    f"chunk {digest.hex()} decoded to {len(raw)}, expected {expected}"
                )
            zero = not any(raw)
            if not zero:
                for offset, length in locations:
                    if os.pwrite(fd, raw, offset) != length:
                        raise SystemExit(f"short write at {offset}")
            with lock:
                state["reused" if reused else "fetched"] += 1
                state["zero" if zero else "placed"] += len(locations)
                total = state["fetched"] + state["reused"]
                if total % 200 == 0 or total == len(unique):
                    elapsed = max(time.time() - started, 1e-6)
                    print(
                        f"    [{total}/{len(unique)}] new={state['fetched']} "
                        f"reused={state['reused']} "
                        f"placed={state['placed']} zero={state['zero']} "
                        f"{total / elapsed:.1f} chunk/s",
                        flush=True,
                    )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, digest, locs) for digest, locs in unique.items()]
            for future in as_completed(futures):
                future.result()
    finally:
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/steamos/rootfs.img"),
        help="assembled ext4 image (default: out/steamos/rootfs.img)",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("out/steamos/chunks"),
        help="chunk cache directory (default: out/steamos/chunks)",
    )
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--refresh", action="store_true", help="re-download the RAUC bundle")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="only report the index statistics; download nothing",
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="skip the final image hash check"
    )
    args = parser.parse_args()

    bundle_dir = args.output.parent / BUNDLE
    manifest_path, caibx = fetch_bundle(bundle_dir.parent, args.refresh)
    sha256, size = read_manifest(manifest_path)
    if sha256 != ROOTFS_SHA256 or size != ROOTFS_SIZE:
        die(f"manifest disagrees with the pinned build: {sha256} / {size}")
    log(f"manifest OK: {BUILD} {VERSION} sha256={sha256[:12]}… size={size}")

    assemble(caibx, args.output, args.cache, args.workers, args.offline)
    if args.offline:
        return

    if not args.skip_verify:
        log("hashing the assembled image")
        digest = sha256_file(args.output)
        if digest != ROOTFS_SHA256:
            die(f"image hash mismatch: {digest}")
        log("image hash matches the RAUC manifest")
    log(f"ready: {args.output}")


if __name__ == "__main__":
    main()
