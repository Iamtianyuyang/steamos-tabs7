#!/usr/bin/env python3
"""Validate copied raw Android super metadata and print JSON/table text only.

Linux host; stdlib only. Input must be a regular copied file, never a device or
symlink. A sufficiently long prefix is enough. --device is text, never opened.
No device mapper operations, mounts, extraction, or writes are performed.
Conservative LP 10.0..2 subset; ambiguous/snapshot layouts are not mapped.
Format: https://android.googlesource.com/platform/system/core/+/refs/heads/main/fs_mgr/liblp/include/liblp/metadata_format.h
"""

import argparse
import hashlib
import json
import os
import re
import stat
import struct

SECTOR = 512
METADATA_BASE = 12288


def require(condition, message):
    if not condition:
        raise ValueError(message)


def open_snapshot(path):
    # O_PATH anchors the inode without opening a block device for reading.
    anchor = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(anchor)
        require(stat.S_ISREG(before.st_mode), "Input must be a regular copied file, not a device/symlink")
        stream = open(f"/proc/self/fd/{anchor}", "rb")
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            stream.close()
            raise ValueError("Snapshot inode changed")
        return stream
    finally:
        os.close(anchor)


def read_at(stream, offset, count):
    size = os.fstat(stream.fileno()).st_size
    require(0 <= offset <= size and 0 <= count <= size - offset,
            f"Snapshot too short at offset {offset}, need {count} bytes")
    stream.seek(offset)
    data = stream.read(count)
    require(len(data) == count, "Snapshot shortened while reading")
    return data


def checksum(data, begin, end):
    return hashlib.sha256(data[:begin] + bytes(end - begin) + data[end:]).digest()


def name(raw):
    head, separator, tail = raw.partition(b"\0")
    require(not separator or not any(tail), "Nonzero name padding")
    value = head.decode("ascii")
    require(re.fullmatch(r"[A-Za-z0-9_]+", value), f"Invalid LP name {value!r}")
    return value


def geometry(stream, offset):
    raw = read_at(stream, offset, 52)
    magic, length = struct.unpack_from("<II", raw)
    require(magic == 0x616C4467 and length == 52, "Invalid/unsupported LP geometry")
    require(checksum(raw, 8, 40) == raw[8:40], "Geometry SHA256 mismatch")
    maximum, slots, block = struct.unpack_from("<III", raw, 40)
    require(512 <= maximum <= 8 * 1024 * 1024 and maximum % SECTOR == 0,
            "Invalid metadata capacity")
    require(1 <= slots <= 8 and SECTOR <= block <= 1024 * 1024 and block % SECTOR == 0,
            "Invalid slots/logical block size")
    return raw, maximum, slots, block


def metadata(stream, offset, maximum, slots, block):
    first = read_at(stream, offset, 128)
    magic, major, minor, header_size = struct.unpack_from("<IHHI", first)
    require(magic == 0x414C5030 and major == 10 and minor <= 2, "Unsupported LP header version")
    require(header_size == (256 if minor == 2 else 128), "Unexpected header size")
    require(header_size <= maximum, "Header exceeds metadata reservation")
    header = read_at(stream, offset, header_size)
    require(checksum(header, 12, 44) == header[12:44], "Header SHA256 mismatch")
    flags = struct.unpack_from("<I", header, 128)[0] if minor == 2 else 0
    require(flags & ~3 == 0, "Unknown header flags")
    require(minor < 2 or not any(header[132:]), "Nonzero reserved header bytes")
    table_size = struct.unpack_from("<I", header, 44)[0]
    require(table_size <= maximum - header_size, "Tables exceed metadata reservation")
    tables = read_at(stream, offset + header_size, table_size)
    require(hashlib.sha256(tables).digest() == header[48:80], "Tables SHA256 mismatch")
    entries, ranges = [], []
    for at, entry_size in ((80, 52), (92, 24), (104, 48), (116, 64)):
        start, count, size = struct.unpack_from("<III", header, at)
        require(size == entry_size and start <= table_size and count <= (table_size - start) // size,
                "Invalid table descriptor")
        if count:
            ranges.append((start, start + count * size))
        entries.append([tables[start + i * size:start + (i + 1) * size] for i in range(count)])
    ranges.sort()
    cursor = 0
    for start, end in ranges:
        require(start == cursor, "Metadata table gap or overlap")
        cursor = end
    require(cursor == table_size, "Unreferenced metadata table bytes")
    partitions, extents, groups, devices = entries
    devs, device_names = [], set()
    for raw in devices:
        first_sector, alignment, alignment_offset, size, raw_name, dev_flags = struct.unpack("<QIIQ36sI", raw)
        dev_name = name(raw_name)
        require(dev_name not in device_names and dev_flags & ~1 == 0,
                "Duplicate device name/unknown device flags")
        device_names.add(dev_name)
        require(size > 0 and size % block == 0 and first_sector * SECTOR < size,
                "Invalid physical device bounds")
        require(first_sector * SECTOR % block == 0, "Unaligned first physical sector")
        require((alignment == 0 and alignment_offset == 0) or
                (alignment > 0 and alignment % SECTOR == 0 and alignment_offset < alignment
                 and alignment_offset % SECTOR == 0), "Invalid physical alignment")
        devs.append(dict(name=dev_name, first_sector=first_sector, size=size,
                         alignment=alignment, alignment_offset=alignment_offset, flags=dev_flags))
    require(devs, "No block devices")
    reservation_end = METADATA_BASE + 2 * slots * maximum
    require(devs[0]["first_sector"] * SECTOR >= reservation_end,
            "Logical extents overlap geometry/metadata reservation")
    group_list, group_names = [], set()
    for raw in groups:
        raw_name, group_flags, limit = struct.unpack("<36sIQ", raw)
        group_name = name(raw_name)
        require(group_name not in group_names and group_flags & ~1 == 0,
                "Duplicate group name/unknown group flags")
        require(limit % block == 0, "Unaligned group capacity")
        group_names.add(group_name)
        group_list.append(dict(name=group_name, flags=group_flags, maximum_size=limit))
    require(group_list, "No partition groups")
    result, names, owned, physical = [], set(), set(), []
    group_usage = [0] * len(group_list)
    for raw in partitions:
        raw_name, attrs, first_extent, count, group = struct.unpack("<36sIIII", raw)
        part_name = name(raw_name)
        attribute_mask = 15 if minor >= 1 else 3
        require(part_name not in names and attrs & ~attribute_mask == 0,
                "Duplicate name/unknown partition attributes for version")
        names.add(part_name)
        require(group < len(groups) and first_extent <= len(extents)
                and count <= len(extents) - first_extent, "Invalid partition indices")
        logical_sector, spans = 0, []
        for index in range(first_extent, first_extent + count):
            require(index not in owned, "Extent shared by multiple partitions")
            owned.add(index)
            sectors, kind, target, source = struct.unpack("<QIQI", extents[index])
            require(sectors > 0 and sectors * SECTOR % block == 0 and kind in (0, 1),
                    "Invalid extent length/alignment/type")
            if kind == 0:
                require(source < len(devs), "Invalid extent source")
                dev = devs[source]
                require(target >= dev["first_sector"] and target * SECTOR % block == 0
                        and sectors <= dev["size"] // SECTOR - target,
                        "Extent outside/aligned incorrectly on physical device")
                physical.append((source, target, target + sectors))
            else:
                require(target == 0 and source == 0, "Invalid zero extent")
            spans.append(dict(logical_sector=logical_sector, sectors=sectors, type=kind,
                              target_sector=target, source=source))
            logical_sector += sectors
        group_usage[group] += logical_sector * SECTOR
        result.append(dict(name=part_name, attributes=attrs, group=group,
                           size=logical_sector * SECTOR, extents=spans))
    require(len(owned) == len(extents), "Unowned extent entries")
    physical.sort()
    require(all(a[0] != b[0] or a[2] <= b[1] for a, b in zip(physical, physical[1:])),
            "Overlapping physical extents")
    for group, usage in zip(group_list, group_usage):
        require(group["maximum_size"] == 0 or usage <= group["maximum_size"],
                "Partitions exceed group capacity")
    return dict(version=f"{major}.{minor}", header_flags=flags, block_devices=devs,
                groups=group_list, partitions=result,
                metadata_sha256=hashlib.sha256(header + tables).hexdigest())


def inspect(args):
    require(re.fullmatch(r"[A-Za-z0-9_]+", args.partition), "Invalid requested partition name")
    require(args.physical_size is None or args.device is not None,
            "--physical-size requires --device")
    with open_snapshot(args.snapshot) as stream:
        require(read_at(stream, 0, 4) != b"\x3a\xff\x26\xed", "Sparse input: first use simg2img")
        geom, maximum, slots, block = geometry(stream, 4096)
        require(geometry(stream, 8192)[0] == geom, "Primary/backup geometry differ")
        require(0 <= args.slot < slots, "Invalid metadata slot")
        primary = metadata(stream, METADATA_BASE + args.slot * maximum, maximum, slots, block)
        backup = metadata(stream, METADATA_BASE + (slots + args.slot) * maximum, maximum, slots, block)
        require(primary == backup, "Primary/backup metadata differ; no mapping table emitted")
    primary["geometry"] = dict(metadata_max_size=maximum, slots=slots, logical_block_size=block)
    matches = [part for part in primary["partitions"] if part["name"] == args.partition]
    require(len(matches) == 1, f"Partition {args.partition!r} not found")
    selected = matches[0]
    if args.device:
        require(re.fullmatch(r"/dev/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", args.device)
                and all(part not in (".", "..") for part in args.device.split("/")),
                "Invalid device path text")
        devs = primary["block_devices"]
        require(len(devs) == 1 and devs[0]["name"] == "super" and devs[0]["flags"] == 0,
                "Mapping only supports one unsuffixed physical super device")
        require(args.physical_size == devs[0]["size"], "Actual super capacity must match metadata")
        require(primary["header_flags"] == 0 and all(part["attributes"] & 4 == 0
                for part in primary["partitions"]), "Snapshot/update/overlay mapping unsupported")
        require(selected["size"] > 0 and selected["attributes"] & (2 | 8) == 0
                and primary["groups"][selected["group"]]["flags"] == 0,
                "Selected partition is empty, disabled or requires slot suffixing")
        primary["readonly_dm_table"] = "\n".join(
            f"{extent['logical_sector']} {extent['sectors']}" +
            (f" linear {args.device} {extent['target_sector']}" if extent["type"] == 0 else " zero")
            for extent in selected["extents"])
        primary["mapping_note"] = ("Text only. Independently verify the live partition identity/capacity; "
                                   "use dmsetup --readonly and ext4 ro,noload.")
    return primary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Copied regular raw super image or metadata prefix")
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--partition", default="vendor")
    parser.add_argument("--device", help="Text only, never opened, e.g. /dev/sda28")
    parser.add_argument("--physical-size", type=int, help="Live super capacity in bytes, verified by caller")
    args = parser.parse_args()
    print(json.dumps(inspect(args), indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, UnicodeError, struct.error) as error:
        raise SystemExit(f"LP inspection refused: {error}")
