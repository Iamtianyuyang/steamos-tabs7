#!/usr/bin/python3
"""Verify the matching Ubuntu source against signed noble-updates metadata."""

import argparse
import hashlib
import io
import json
import lzma
from pathlib import Path
import stat
import subprocess
import tempfile

VERSION = '1.46.0-1ubuntu2.8'
FINGERPRINT = 'F6ECB3762474EDA9D21B7022871920D1991BC93C'
SOURCE_FILES = {'network-manager_1.46.0.orig.tar.xz',
                'network-manager_1.46.0-1ubuntu2.8.debian.tar.xz',
                'network-manager_1.46.0-1ubuntu2.8.dsc'}


def read_regular(path, limit):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        raise RuntimeError('Invalid verification input: ' + str(path))
    data = path.read_bytes()
    if len(data) != info.st_size:
        raise RuntimeError('Verification input changed: ' + str(path))
    return data


def stanza_fields(text):
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith((' ', '\t')):
            if key is None:
                raise RuntimeError('Malformed Debian metadata continuation')
            fields[key] += '\n' + line.strip()
        elif ':' in line:
            key, value = line.split(':', 1)
            if key in fields:
                raise RuntimeError('Duplicate Debian metadata field')
            fields[key] = value.strip()
    return fields


def checksum_rows(value):
    result = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        digest, size, name = line.split()
        if name in result or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise RuntimeError('Invalid SHA256 metadata entry')
        result[name] = (digest, int(size))
    return result


def verify(directory, keyring=Path('/usr/share/keyrings/ubuntu-archive-keyring.gpg')):
    directory = directory.resolve(strict=True)
    release_input = directory / 'InRelease'
    inrelease = read_regular(release_input, 1024 * 1024)
    with tempfile.TemporaryDirectory(prefix='tabs7-nm-verify-') as temporary:
        release_path = Path(temporary) / 'Release'
        completed = subprocess.run(
            ['gpgv', '--keyring', str(keyring), '--status-fd', '1',
             '--output', str(release_path), str(release_input)],
            capture_output=True, text=True, timeout=30, check=True)
        if not any(line.startswith('[GNUPG:] VALIDSIG ' + FINGERPRINT + ' ')
                   for line in completed.stdout.splitlines()):
            raise RuntimeError('Unexpected Ubuntu archive signing fingerprint')
        release = stanza_fields(release_path.read_text())
    if release.get('Origin') != 'Ubuntu' or release.get('Suite') != 'noble-updates':
        raise RuntimeError('Expected official Ubuntu noble-updates metadata')
    sources = read_regular(directory / 'Sources.xz', 64 * 1024 * 1024)
    expected_hash, expected_size = checksum_rows(release['SHA256'])['main/source/Sources.xz']
    if len(sources) != expected_size or hashlib.sha256(sources).hexdigest() != expected_hash:
        raise RuntimeError('Sources index does not match signed Release')
    with lzma.LZMAFile(io.BytesIO(sources)) as stream:
        plain = stream.read(128 * 1024 * 1024 + 1)
    if len(plain) > 128 * 1024 * 1024:
        raise RuntimeError('Sources index exceeds the supported size')
    matches = []
    for stanza in plain.decode().split('\n\n'):
        if stanza.startswith('Package: network-manager\n'):
            fields = stanza_fields(stanza)
            if fields.get('Version') == VERSION:
                matches.append(fields)
    if len(matches) != 1 or matches[0].get('Directory') != 'pool/main/n/network-manager':
        raise RuntimeError('Exact matching NetworkManager source was not found')
    files = checksum_rows(matches[0]['Checksums-Sha256'])
    if set(files) != SOURCE_FILES:
        raise RuntimeError('Unexpected matching source archive set')
    records = []
    for name, (digest, size) in sorted(files.items()):
        data = read_regular(directory / name, 64 * 1024 * 1024)
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError('Source archive SHA256/size mismatch: ' + name)
        records.append({'path': name, 'size': size, 'sha256': digest})
    return {'package': 'network-manager', 'version': VERSION, 'suite': 'noble-updates',
            'signing_fingerprint': FINGERPRINT,
            'inrelease_sha256': hashlib.sha256(inrelease).hexdigest(),
            'sources_sha256': expected_hash, 'files': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('verification_directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.verification_directory), sort_keys=True, indent=2))
