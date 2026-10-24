#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
debs="$project_root/out/tools/debs"
mkdir -p "$debs" "$project_root/out/tools/llvm"

# Keep linker installation local; the host must already provide Clang/LLVM 18.
if ! command -v ld.lld-18 >/dev/null && [[ ! -x "$project_root/out/tools/llvm/usr/bin/ld.lld-18" ]]; then
    (cd "$debs" && apt-get download lld-18)
    lld_packages=("$debs"/lld-18_*.deb)
    dpkg-deb -x "${lld_packages[0]}" "$project_root/out/tools/llvm"
fi

busybox_name=busybox-static_1.36.1-6ubuntu3.1_arm64.deb
if ! dpkg-deb -f "$debs/$busybox_name" Architecture 2>/dev/null | grep -qx arm64; then
    busybox_path="pool/main/b/busybox/$busybox_name"
    curl --fail --location --retry 2 --connect-timeout 10 --max-time 120 \
        --output "$debs/$busybox_name.download" \
        "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports/$busybox_path"
    [[ $(dpkg-deb -f "$debs/$busybox_name.download" Architecture) == arm64 ]]
    mv "$debs/$busybox_name.download" "$debs/$busybox_name"
fi
printf 'Project linker and ARM64 BusyBox are ready.\n'
