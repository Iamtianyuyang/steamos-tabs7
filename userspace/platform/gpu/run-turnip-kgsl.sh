#!/bin/sh
# Explicit per-command ICD selection. Does not select a desktop renderer.
set -eu
task_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
task_prefix=${TABS7_TURNIP_PREFIX:-$task_dir/install-turnip-kgsl}
task_manifest=$task_prefix/turnip-kgsl.icd.json
[ "$#" -gt 0 ] || { printf 'Usage: %s COMMAND [ARG ...]\n' "$0" >&2; exit 2; }
[ -r "$task_manifest" ] || { printf 'Private ICD missing: %s\n' "$task_manifest" >&2; exit 1; }
export VK_DRIVER_FILES=$task_manifest
# Compatibility with older Vulkan loaders; both select the same single ICD.
export VK_ICD_FILENAMES=$task_manifest
exec "$@"
