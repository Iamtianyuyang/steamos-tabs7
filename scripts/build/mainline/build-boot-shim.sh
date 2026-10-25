#!/usr/bin/env bash
# Build the BOOT shim (boot/shim/) around a mainline Image and DTB.
# Offline. Output: out/boot-shim/Image, packed with
#   build-steamos-6.18-boot.py --experiment --kernel out/boot-shim/Image \
#       --dtb <vendor DTB from the deployed BOOT> ...
#
# Usage: build-boot-shim.sh [Image] [mainline DTB]
#   (defaults: out/bringup/Image and out/bringup/sm8250-samsung-gts7lwifi.dtb)
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
project_id=$(printf '%s' "$project_root" | sha256sum | cut -c1-12)
kernel=$(realpath "${1:-$project_root/out/bringup/Image}")
dtb=$(realpath "${2:-$project_root/out/bringup/sm8250-samsung-gts7lwifi.dtb}")
out_dir=$project_root/out/boot-shim
# Paths with spaces break .incbin quoting and Kbuild alike; build in the cache.
work=${XDG_CACHE_HOME:-$HOME/.cache}/tabs7linux/boot-shim-$project_id
reserve=$((20 << 20))
export PATH="$project_root/out/tools/llvm/usr/bin:$PATH"

rm -rf "$work"
mkdir -p "$work/obj" "$out_dir"
cp "$kernel" "$work/Image"
cp "$dtb" "$work/mainline.dtb"
cp -r "$project_root/boot/shim/." "$work/src/"
cp "$project_root"/linux-sm8250-v6.18/scripts/dtc/libfdt/{fdt.c,fdt_ro.c,fdt_rw.c,fdt_wip.c,fdt.h,libfdt.h,libfdt_env.h,libfdt_internal.h} "$work/src/"
cat > "$work/src/payload.S" <<EOF
	.section .payload.dtb, "a"
	.incbin "$work/mainline.dtb"
	.section .payload.kernel, "a"
	.incbin "$work/Image"
EOF

cflags=(--target=aarch64-none-elf -ffreestanding -fno-builtin -nostdlib -O2
        -mgeneral-regs-only -mstrict-align -fno-pic -fno-pie -mcmodel=small
        -fno-stack-protector -fno-asynchronous-unwind-tables -fno-unwind-tables
        -DSHIM_RESERVE=$reserve -I"$work/src/include" -I"$work/src" -Wall)
objects=()
for source in start.S payload.S shim.c string.c fdt.c fdt_ro.c fdt_rw.c fdt_wip.c; do
    object=$work/obj/${source%.*}.o
    clang-18 "${cflags[@]}" -c "$work/src/$source" -o "$object"
    objects+=("$object")
done

# MMU off, load address unknown: only PC-relative references may survive.
if llvm-readelf-18 -r "${objects[@]}" | grep -E 'R_AARCH64_(ABS64|ABS32|PREL64)' ; then
    echo 'absolute relocation in the shim; it would break at a different load address' >&2
    exit 1
fi
ld.lld-18 -T "$work/src/shim.lds" -nostdlib --no-pie "${objects[@]}" -o "$work/shim.elf"
llvm-objcopy-18 -O binary "$work/shim.elf" "$work/shim.bin"

python3 - "$work/shim.bin" "$out_dir/Image" "$reserve" "$kernel" "$dtb" <<'PY'
import hashlib
import struct
import sys
src, dst, reserve, kernel, dtb = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
data = bytearray(open(src, 'rb').read())
inner = open(kernel, 'rb').read()
assert data[0x200000:0x200000 + len(inner)] == inner, 'Image not at the 2 MiB offset'
text_offset, image_size = struct.unpack_from('<QQ', data, 8)
assert struct.unpack_from('<I', data, 0x38)[0] == 0x644d5241
assert text_offset == 0x80000 and image_size == reserve
inner_size = struct.unpack_from('<Q', inner, 16)[0]
# Worst case: Image moved to base + 4 MiB, 1 MiB DTB after it, stack at the end.
need = 0x400000 + inner_size + 0x10000 + 0x100000 + 0x10000
assert need <= reserve, f'reserve {reserve:#x} too small, need {need:#x}'
assert len(data) <= reserve
data += b'\0' * (reserve - len(data))
open(dst, 'wb').write(data)
print(f'shim Image: {len(data)} bytes sha256 {hashlib.sha256(data).hexdigest()}')
print(f'  inner Image {len(inner)} bytes (image_size {inner_size:#x}), DTB {dtb}')
PY
