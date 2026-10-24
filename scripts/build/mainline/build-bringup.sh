#!/usr/bin/env bash
set -euo pipefail

project_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)
project_id=$(printf '%s' "$project_root" | sha256sum | cut -c1-12)
build_root=${TAB_S7_BUILD_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/tabs7linux/$project_id}
target=${1:-dtb}
jobs=${JOBS:-$(nproc)}
if (( jobs > 12 )); then jobs=12; fi

case "$target" in
    dtb|image) ;;
    *) printf 'Usage: %s [dtb|image]\n' "$0" >&2; exit 2 ;;
esac
case "$build_root" in
    *' '*|*':'*) echo 'TAB_S7_BUILD_ROOT must not contain spaces or colons.' >&2; exit 2 ;;
esac

# Kbuild rejects source paths with spaces. Keep edits in the project and build
# a synchronized copy in a separate directory, with all outputs out of tree.
mkdir -p "$build_root/src" "$build_root/obj" "$project_root/out/bringup" "$project_root/out/logs"
export PATH="$project_root/out/tools/llvm/usr/bin:$PATH"
for tool in rsync make clang-18 ld.lld-18 llvm-ar-18 llvm-nm-18 llvm-objcopy-18 llvm-strip-18; do
    command -v "$tool" >/dev/null || { echo "Missing tool: $tool" >&2; exit 1; }
done
rsync -a --delete --exclude=.git/ "$project_root/linux-sm8250-v6.2/" "$build_root/src/"
cp "$project_root/hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config" "$build_root/bringup.config"

kmake=(make -C "$build_root/src" O="$build_root/obj" ARCH=arm64 LLVM=-18)
"${kmake[@]}" KCONFIG_ALLCONFIG="$build_root/bringup.config" allnoconfig

# Kconfig can silently drop a request when dependencies are missing. Reject
# incomplete configurations before claiming an image contains the USB/log path.
python3 - "$build_root/bringup.config" "$build_root/obj/.config" <<'PY'
from pathlib import Path
import sys
requested = [line for line in Path(sys.argv[1]).read_text().splitlines()
             if line.startswith('CONFIG_')]
actual = set(Path(sys.argv[2]).read_text().splitlines())
missing = [line for line in requested if line not in actual]
if missing:
    raise SystemExit('Kconfig did not retain:\n' + '\n'.join(missing))
print(f'All {len(requested)} requested configuration settings retained.')
PY

"${kmake[@]}" -j"$jobs" qcom/sm8250-samsung-gts7lwifi.dtb
cp "$build_root/obj/arch/arm64/boot/dts/qcom/sm8250-samsung-gts7lwifi.dtb" "$project_root/out/bringup/"
if [[ "$target" == image ]]; then
    "${kmake[@]}" -j"$jobs" Image.gz
    cp "$build_root/obj/arch/arm64/boot/Image" "$build_root/obj/arch/arm64/boot/Image.gz" "$project_root/out/bringup/"
fi
cp "$build_root/obj/.config" "$project_root/out/bringup/kernel.config"
printf 'Build directory: %s\nArtifacts: %s/out/bringup/\n' "$build_root" "$project_root"
