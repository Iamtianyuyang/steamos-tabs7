#!/usr/bin/env python3
"""Download and verify pinned Android 13 AOSP boot-image utilities locally."""
import base64
import hashlib
from pathlib import Path
import urllib.request

REVISION = '6cb944b82c819b691bc21d8228e155f1db07a446'
FILES = {
    'unpack_bootimg.py': '50ef727dc41402430cf68933eaf5b7c7c71d91d2cc3cf908b2ea8e6e1faa4595',
    'mkbootimg.py': '37d84b3d162e0bc62e36c1f4e1c63c85ea0caa9f29be023eb2f8efe006ad948c',
    'gki/generate_gki_certificate.py': '6ee0b2e3c3323ae8fccf326bf9a526451eac977005efabf7d129b3063bcfb945',
}


def main():
    destination = Path(__file__).resolve().parents[2] / 'out/tools/mkbootimg'
    for name, expected in FILES.items():
        path = destination / name
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            url = ('https://android.googlesource.com/platform/system/tools/mkbootimg/+/'
                   + REVISION + '/' + name + '?format=TEXT')
            with urllib.request.urlopen(url, timeout=30) as response:
                data = base64.b64decode(response.read(), validate=True)
            if hashlib.sha256(data).hexdigest() != expected:
                raise SystemExit(f'AOSP source checksum mismatch: {name}')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        print(f'Verified AOSP {REVISION[:12]}: {name}')


if __name__ == '__main__':
    main()
