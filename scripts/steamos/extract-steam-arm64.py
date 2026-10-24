#!/usr/bin/env python3
"""Install only Valve's ARM64 client runtime, preserving any previous runtime."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import stat
import tempfile
import uuid
import zipfile


def relative_name(name: str) -> PurePosixPath:
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or '..' in path.parts
            or ':' in path.parts[0] or path.parts[0] != 'steamrtarm64'):
        raise ValueError(f'Unexpected client archive path: {name!r}')
    return path


def install(archive_path: Path, destination: Path,
            additional_archives: tuple[Path, ...] = ()) -> Path | None:
    # Validate names and links before creating a destination or replacing files.
    with ExitStack() as stack:
        entries = []
        seen = {}
        for source in (archive_path, *additional_archives):
            archive = stack.enter_context(zipfile.ZipFile(source))
            for entry in archive.infolist():
                name = relative_name(entry.filename)
                mode = entry.external_attr >> 16
                directory = entry.is_dir() or stat.S_ISDIR(mode)
                link = stat.S_ISLNK(mode)
                kind = 'directory' if directory else 'link' if link else 'file'
                if name in seen and not (kind == seen[name] == 'directory'):
                    raise ValueError(f'Duplicate archive path: {name}')
                seen[name] = kind
                target = None
                if link:
                    target = archive.read(entry).decode('utf-8').replace('\\', '/')
                    resolved = posixpath.normpath(posixpath.join(str(name.parent), target))
                    if (target.startswith('/') or ':' in target or
                            not resolved.startswith('steamrtarm64/')):
                        raise ValueError(f'Client library link escapes its runtime: {name}')
                entries.append((archive, entry, name, kind, target))
        if seen.get(PurePosixPath('steamrtarm64/steam')) != 'file':
            raise ValueError('Archive has no ARM64 Steam executable')
        for _, _, name, _, _ in entries:
            if any(seen.get(parent) in ('file', 'link') for parent in name.parents):
                raise ValueError(f'Archive parent is not a directory: {name}')

        destination.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.steam-client-', dir=destination) as temporary:
            staging = Path(temporary).resolve()
            for archive, entry, name, kind, _ in entries:
                path = staging / name
                path.parent.mkdir(parents=True, exist_ok=True)
                if kind == 'directory':
                    path.mkdir(exist_ok=True)
                elif kind == 'file':
                    with archive.open(entry) as source, path.open('wb') as output:
                        shutil.copyfileobj(source, output)
                    with path.open('rb') as source:
                        header = source.read(4)
                    path.chmod(0o755 if header == b'\x7fELF' or header[:2] == b'#!' else 0o644)
            for _, _, name, kind, target in entries:
                if kind == 'link':
                    path = staging / name
                    path.symlink_to(target)
                    if not path.resolve().is_relative_to(staging / 'steamrtarm64'):
                        raise ValueError(f'Library link chain escapes its runtime: {name}')
                    if not path.is_file():
                        raise ValueError(f'Unresolved client library link: {name}')
            executable = staging / 'steamrtarm64/steam'
            with executable.open('rb') as source:
                header = source.read(20)
            if (header[:6] != b'\x7fELF\x02\x01' or
                    int.from_bytes(header[18:20], 'little') != 183):
                raise ValueError('Steam executable is not little-endian AArch64 ELF64')
            runtime = destination / 'steamrtarm64'
            backup = None
            if runtime.exists() or runtime.is_symlink():
                backup = destination / ('steamrtarm64.before-' + uuid.uuid4().hex[:12])
                runtime.rename(backup)
            try:
                os.replace(staging / 'steamrtarm64', runtime)
            except OSError:
                if backup:
                    backup.rename(runtime)
                raise
            return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('additional_archives', type=Path, nargs='*',
                        help='matching ARM64 CEF/SDL runtime archives')
    args = parser.parse_args()
    try:
        backup = install(args.archive, args.destination, tuple(args.additional_archives))
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        parser.exit(1, f'Steam extraction failed: {error}\n')
    if backup:
        print(f'Previous runtime preserved at {backup}')


if __name__ == '__main__':
    main()
