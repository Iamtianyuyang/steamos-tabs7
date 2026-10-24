# Tab S7 Linux port

- Device: Samsung SM-T870 / gts7lwifi, hardware revision 7.
- Project version: **0.0.001**, kept in `VERSION`. Do not change the version
  number, retag releases or invent new version names unless the user asks for it
  explicitly; a hardware result alone is not a reason to bump it.
- **Project goal: the full SteamOS ARM port on the mainline 6.2 kernel.**
  SteamOS (Game Mode and Desktop Mode) with the two-VIG display path. Everything
  else in the tree exists only to serve that goal; the Ubuntu GNOME/Phosh/XFCE
  desktop track was removed in the SteamOS-only cleanup.
- Kernel: `linux-sm8250-v6.2/` is the target kernel tree and the canonical DTS
  location (`arch/arm64/boot/dts/qcom/`), built with
  `scripts/build/mainline/`. Mainline 6.2 has **never booted on this hardware**;
  every hardware result recorded so far was obtained with the vendor
  Lineage/Samsung 4.19 kernel, whose path (`scripts/build/vendor/`,
  `scripts/build/bluetooth/`) stays available until 6.2 reaches the same state.
  Never describe a 6.2 result as a hardware result without a real boot log.
- Historical drafts are in `hardware/samsung/gts7lwifi/devicetree/drafts/`;
  downstream references and overlays are in its sibling `vendor/` and `overlays/`.
- Follow [the project layout](docs/development/project-layout.md): host build and
  device workflows are under `scripts/`, device data under `hardware/`, initramfs
  under `boot/`, SteamOS session sources under `userspace/steamos/`, shared host
  services under `userspace/platform/`. Keep the kernel tree in its existing root
  location. Installed paths such as `/usr/local/lib/tabs7linux/` and historical
  evidence under `out/` are independent of the repository source layout.
- SteamOS work status, rootfs provenance and acceptance boundaries live in
  [the SteamOS document](docs/steamos/README.md). Native boot evidence lives in
  [docs/boot/native-boot-tests.md](docs/boot/native-boot-tests.md). Mainline 6.2
  remains unverified on hardware.
- Use project skills `device-tree`, `cross-gcc`, `device-drivers`, and
  `kernel-debugging` when applicable.
- Prefer codebase-memory MCP for code discovery; run `index_repository` for the
  directory you need (indexes are rebuilt on demand after the 0.12 upgrade; no
  index is kept by default). DTS, Kconfig, logs, and other literal configuration
  searches can use native tools.
- The workspace path contains spaces, so Kbuild cannot run in place.
  `scripts/build/mainline/build-bringup.sh` synchronizes the 6.2 tree into
  `~/.cache/tabs7linux/<project-path-hash>/` and builds there; `TAB_S7_BUILD_ROOT`
  overrides the location. Vendor 4.19 builds (`scripts/build/vendor/`) work from
  the prepared source under `out/vendor-kernel-prep/src`, which the SteamOS
  IPC-kernel verification also reads.
- Keep downloaded tools, build artifacts, device data, and partition backups in
  ignored `out/`. Keep the downstream reference Git repository independent.
- `python3 scripts/device/collect-device.py` reads device facts. `--backup`
  requires root ADB and reads boot/dtbo/recovery/vbmeta; it never flashes partitions. `--output`
  separates snapshots of different boot environments inside ignored `out/`.
- `scripts/device/inspect-firmware.py` uses the matching factory AP archive and
  checks component hashes and reservations against
  `hardware/samsung/gts7lwifi/profiles/`. Firmware originals are recovery
  resources, not actual device partition dumps.
- `scripts/recovery/build-recovery-draft.py` preserves factory v2 header parameters by
  default; `--header-source lineage` selects the working official Recovery
  header. Both preserve recovery DTBO table slots. Native boot is unverified.
- Before writing a project-built Linux boot/recovery image, verify available
  original partition backups, boot image layout, live DTB reservations, and
  original DTBO handling.
- Installing official root Recovery may precede live FDT and partition dumps
  when stock ADB cannot read them. First verify matching factory restore images,
  the official recovery hash, live PIT capacities, and unlocked device identity.
  User authorization for a required data reset must already exist. Keep boot
  and dtbo intact during this bootstrap. A later recovery dump contains the
  newly installed recovery, not the overwritten original.
- Distinguish source compilation, userspace running under Android, and native
  Linux boot on hardware. A checked item must name the evidence it represents.
- `scripts/build/vendor/build-vendor-linux.py` builds the native BusyBox control
  using the working official Lineage 4.19 kernel/DTB/DTBO. This has booted on hardware:
  PID 1 is BusyBox, rootfs is RAM, USB RNDIS/ACM and telnet work, and the full
  RECOVERY readback matches. The SteamOS user space runs on top of that kernel
  path; the independent systemd Game Mode session is still being adapted.
