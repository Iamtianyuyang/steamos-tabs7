#!/usr/bin/env python3
"""Fetch pinned AVB tooling and its published development test key."""

import hashlib
import json
from pathlib import Path
import urllib.request

REVISION = 'cb5eae91a6b83e1394882decbf6de133239107e1'
FILES = {
    'avbtool.py': 'aa080bb3dd613cbbcaea410a8cff62225811fc79a4af05613a63ec3acec7ab92',
    'test/data/testkey_rsa4096.pem': '6a224754880a57ab9cbd308267cd157d94cf05a1c8cb851aec4090e045d24121',
}


def main():
    destination = Path(__file__).resolve().parents[2] / 'out/tools/avb'
    destination.mkdir(parents=True, exist_ok=True)
    records = {}
    for name, digest in FILES.items():
        path = destination / Path(name).name
        url = f'https://raw.githubusercontent.com/LineageOS/android_external_avb/{REVISION}/{name}'
        if path.exists():
            data = path.read_bytes()
        else:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f'AVB tool hash mismatch: {name}')
        if not path.exists():
            path.write_bytes(data)
        records[name] = {'sha256': digest, 'url': url}
    (destination / 'source.json').write_text(json.dumps({
        'commit': REVISION, 'files': records,
        'key_note': 'Public AOSP development test key; never a production credential',
    }, indent=2) + '\n')
    print('Pinned AVB tool and published development key verified.')


if __name__ == '__main__':
    main()
