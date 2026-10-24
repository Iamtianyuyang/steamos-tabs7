#!/bin/bash
# Compile/stage only. This does not stop/start a daemon or install into live /etc.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

VERSION=1.46.0-1ubuntu2.8
DIST_VERSION=$VERSION+tabs7linux-zero-gateway
PREFIX=/usr/local/lib/tabs7linux/networkmanager-1.46
ORIGINAL_SHA=e0a2e91e2b1a942488e4bf8b3b6e72362bfb297ded82fd5eb50b66ed8001ab99
PATCHED_SHA=baa746045c1aa9ed2118d22db3be9364d16c4f1717b7e68de30b5b14ca231f2d
SELF_DIR=$(dirname "$(realpath "$0")")

usage() {
    printf '%s\n' 'Usage: build-networkmanager-private.sh SOURCE BUILD STAGE [JOBS]' \
        'Run inside native Ubuntu arm64; BUILD and STAGE must be new directories.' \
        'SOURCE must be the verified, Ubuntu-patched source; ../verification holds signed inputs.' \
        'Dependencies: build-essential meson ninja-build pkg-config gettext xsltproc' \
        'libglib2.0-dev uuid-dev libudev-dev libdbus-1-dev libndp-dev libgnutls28-dev' \
        'libpolkit-gobject-1-dev libsystemd-dev libcurl4-gnutls-dev libpsl-dev'
}
if [[ ${1:-} == --help || $# == 0 ]]; then usage; exit 0; fi
[[ $# == 3 || $# == 4 ]] || { usage >&2; exit 1; }
[[ $(uname -m) == aarch64 && $(dpkg --print-architecture) == arm64 ]] || \
    { printf 'This builder requires native Ubuntu arm64.\n' >&2; exit 1; }
for package in network-manager libnm0; do
    [[ $(dpkg-query -W -f='${Version}' "$package") == "$VERSION" ]] || \
        { printf 'Installed %s does not match %s.\n' "$package" "$VERSION" >&2; exit 1; }
done
for tool in cc meson ninja pkg-config python3 gpgv patch sha256sum xsltproc msgfmt perl; do
    command -v "$tool" >/dev/null || { printf 'Missing build tool: %s\n' "$tool" >&2; exit 1; }
done
for module in gio-unix-2.0 gmodule-2.0 uuid libudev dbus-1 libndp gnutls polkit-gobject-1 libsystemd libcurl libpsl; do
    pkg-config --exists "$module" || { printf 'Missing development module: %s\n' "$module" >&2; exit 1; }
done

NM_SOURCE=$(realpath "$1")
NM_BUILD=$(realpath -m "$2")
NM_STAGE=$(realpath -m "$3")
JOBS=${4:-2}
[[ $JOBS =~ ^[1-9][0-9]*$ && $JOBS -le 8 ]] || { printf 'JOBS must be 1..8.\n' >&2; exit 1; }
[[ ! -e $NM_BUILD && ! -L $NM_BUILD && ! -e $NM_STAGE && ! -L $NM_STAGE ]] || \
    { printf 'BUILD/STAGE already exist; use fresh output directories.\n' >&2; exit 1; }
[[ $NM_BUILD != / && $NM_STAGE != / && $NM_BUILD != "$NM_STAGE" && \
   $NM_BUILD != "$NM_SOURCE" && $NM_STAGE != "$NM_SOURCE" && \
   $NM_BUILD != "$NM_SOURCE/"* && $NM_STAGE != "$NM_SOURCE/"* && \
   $NM_SOURCE != "$NM_BUILD/"* && $NM_SOURCE != "$NM_STAGE/"* && \
   $NM_STAGE != "$NM_BUILD/"* && $NM_BUILD != "$NM_STAGE/"* ]] || \
    { printf 'Source/build/stage directories must be separate.\n' >&2; exit 1; }
IFS= read -r changelog < "$NM_SOURCE/debian/changelog"
[[ $changelog == "network-manager ($VERSION) "* ]] || \
    { printf 'Unexpected Ubuntu source version.\n' >&2; exit 1; }
python3 "$SELF_DIR/verify-networkmanager-source.py" "$NM_SOURCE/../verification"
platform_source=$NM_SOURCE/src/libnm-platform/nm-linux-platform.c
current_sha=$(sha256sum "$platform_source")
current_sha=${current_sha%% *}
if [[ $current_sha == "$ORIGINAL_SHA" ]]; then
    patch --batch --fuzz=0 -d "$NM_SOURCE" -p1 < "$SELF_DIR/NetworkManager-1.46-zero-gateway.patch"
elif [[ $current_sha != "$PATCHED_SHA" ]]; then
    printf 'Platform source differs from the matching original/patched file.\n' >&2
    exit 1
fi
printf '%s  %s\n' "$PATCHED_SHA" "$platform_source" | sha256sum --check

meson setup "$NM_BUILD" "$NM_SOURCE" --wrap-mode=nodownload \
    --prefix="$PREFIX" --sysconfdir=/etc --localstatedir=/var \
    --libdir=lib --libexecdir=libexec --buildtype=release \
    -Ddist_version="$DIST_VERSION" -Dconfig_plugins_default=keyfile,ifupdown \
    -Dwifi=true -Diwd=false -Dwext=true -Difupdown=true -Dnetplan=false \
    -Dsession_tracking=systemd -Dsession_tracking_consolekit=false -Dsuspend_resume=systemd \
    -Dpolkit=true -Dcrypto=gnutls -Dconcheck=true -Dlibpsl=true \
    -Dsystemdsystemunitdir=no -Dudev_dir=no -Ddbus_conf_dir=/etc/dbus-1/system.d \
    -Dnmcli=false -Dnmtui=false -Dnm_cloud_setup=false -Dreadline=none \
    -Dppp=false -Dmodem_manager=false -Dofono=false -Dbluez5_dun=false \
    -Dteamdctl=false -Dovs=false -Dfirewalld_zone=false -Debpf=false \
    -Dselinux=false -Dlibaudit=no -Dqt=false -Ddocs=false -Dtests=no \
    -Dintrospection=false -Dvapi=false
python3 - "$NM_BUILD/config-extra.h" "$PREFIX" "$DIST_VERSION" <<'PY'
from pathlib import Path
import re, sys
text = Path(sys.argv[1]).read_text()
expected = {'NMCONFDIR': '/etc/NetworkManager', 'NMSTATEDIR': '/var/lib/NetworkManager',
            'NMRUNDIR': '/var/run/NetworkManager',
            'NMPLUGINDIR': sys.argv[2] + '/lib/NetworkManager/' + sys.argv[3]}
for key, value in expected.items():
    if not re.search(r'^#define\s+' + key + r'\s+"' + re.escape(value) + r'"$', text, re.M):
        raise RuntimeError('Unexpected compiled runtime path: ' + key)
print('Private plugins and existing /etc /var/run paths verified.')
PY
meson compile -C "$NM_BUILD" -j "$JOBS"
meson install -C "$NM_BUILD" --no-rebuild --destdir "$NM_STAGE"

# Retain the private executable/plugins/helpers. The staged global configuration,
# policy and state directories are never included in the deployment tar.
# Meson has no libnm=false option: discard only its private client ABI outputs.
python3 - "$NM_STAGE$PREFIX" "$VERSION" "$DIST_VERSION" "$PATCHED_SHA" <<'PY'
from pathlib import Path
import hashlib, json, shutil, struct, sys
root = Path(sys.argv[1])
for name in ('include', 'lib/pkgconfig'):
    directory = root / name
    if directory.exists():
        shutil.rmtree(directory)
for file in (root / 'lib').glob('libnm.so*'):
    file.unlink()
required = [root / 'sbin/NetworkManager',
            root / 'lib/NetworkManager' / sys.argv[3] / 'libnm-device-plugin-wifi.so']
for file in required:
    data = file.read_bytes()
    if data[:7] != b'\x7fELF\x02\x01\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise RuntimeError('Expected an AArch64 executable/plugin: ' + str(file))
records = []
for file in sorted(root.rglob('*')):
    if file.is_symlink():
        if not file.resolve(strict=True).is_relative_to(root.resolve()):
            raise RuntimeError('Private payload contains an external symlink: ' + str(file))
    elif file.is_file():
        records.append({'path': str(file.relative_to(root)), 'size': file.stat().st_size,
                        'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
(root / 'tabs7linux-build.json').write_text(json.dumps(
    {'source_version': sys.argv[2], 'dist_version': sys.argv[3],
     'platform_source_sha256': sys.argv[4], 'libnm_deployed': False,
     'files': records}, sort_keys=True, indent=2) + '\n')
print('Staged private executable/plugins validated; distro libnm was not installed over.')
PY
"$NM_STAGE$PREFIX/sbin/NetworkManager" --version
tar -C "$NM_STAGE" -czf "$NM_BUILD/networkmanager-private.tar.gz" "${PREFIX#/}"
sha256sum "$NM_BUILD/networkmanager-private.tar.gz"
printf 'Build complete. Deployment payload: %s/networkmanager-private.tar.gz\n' "$NM_BUILD"
