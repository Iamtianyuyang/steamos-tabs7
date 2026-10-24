#!/usr/bin/env bash
# Assemble boot/probe/entry-probe.S into a raw arm64 Image under out/entry-probe/.
# Offline; pack it with build-steamos-6.18-boot.py --experiment.
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
out_dir=$project_root/out/entry-probe
export PATH="$project_root/out/tools/llvm/usr/bin:$PATH"
mkdir -p "$out_dir"

clang-18 --target=aarch64-linux-gnu -c "$project_root/boot/probe/entry-probe.S" -o "$out_dir/entry-probe.o"
ld.lld-18 -Ttext=0 -e _start "$out_dir/entry-probe.o" -o "$out_dir/entry-probe.elf"
llvm-objcopy-18 -O binary "$out_dir/entry-probe.elf" "$out_dir/Image"

python3 - "$out_dir/Image" <<'PY'
import struct
import sys
data = open(sys.argv[1], 'rb').read()
text_offset, image_size, flags = struct.unpack_from('<QQQ', data, 8)
magic = struct.unpack_from('<I', data, 0x38)[0]
res3 = struct.unpack_from('<Q', data, 0x28)[0]
assert magic == 0x644d5241, hex(magic)
assert text_offset == 0x80000 and res3 == 0xecefecef and image_size == len(data), (text_offset, res3, image_size, len(data))
print(f'Image: {len(data)} bytes, text_offset=0x{text_offset:x} res3=0x{res3:x} flags=0x{flags:x}')
PY
