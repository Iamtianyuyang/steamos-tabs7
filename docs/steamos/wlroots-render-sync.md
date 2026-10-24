# SM-T870 GLES render completion

`wlroots-sm-t870-render-sync.patch` adds a private, opt-in completion wait to
the GLES buffer render-pass submission in Ubuntu Noble's wlroots 0.17.1.
Set `TABS7_WLROOTS_RENDER_FINISH=1` on the Phoc process to enable it. The value
is read once when creating each GLES renderer; an unset value or any value
other than exactly `1` retains the original behavior.

The native SM-T870 GNOME desktop showed local flashing/corruption during
interaction at 2560×1600 with a 120 Hz physical output. A temporary Phoc-only
`glFlush()` interceptor that called the real `glFlush()` followed by
`glFinish()` removed the artifacts in the user's test. The physical mode
remained 1600×2560, rotated 90 degrees, with the same two VIG planes and
119.9998 Hz mode timing. This supports waiting for rendering completion;
it does not by itself identify every fence in the KGSL-to-display path.

The source patch narrows the wait to `render_pass_submit()`: immediately
after its existing `glFlush()`, `glFinish()` completes the GPU work before
wlroots can submit that buffer to KMS. Both VIG planes still use one common
framebuffer in one atomic request. Geometry, buffer ownership, page-flip
handling, and public APIs are unchanged. The new booleans are in the private
renderer structure; the public SONAME and exported symbols stay the same.

After its first actual wait returns, each enabled renderer writes one
informational marker directly to standard error:

```
tabs7linux: SM-T870 GLES2 render completion wait active
```

This marker bypasses Phoc's default wlroots INFO filtering without enabling
global verbose logs or reporting an error. The startup check must require
the completion text for the enabled GPU profile, so
an older library cannot silently ignore the environment switch. There is no
per-frame logging. GNOME, Xwayland and system GLES libraries are unchanged.
The temporary interceptor must be removed when validating this source patch.

## Build the existing private source

Apply the separate completion patch after the Ubuntu layer-shell compatibility
patch and `wlroots-sm-t870-split.patch`. For the existing, already configured
native ARM64 source tree:

```sh
test "$(uname -m)" = aarch64
cd /usr/local/src/tabs7linux/wlroots-0.17.1-sm-t870
patch -p1 --forward --dry-run < /path/to/wlroots-sm-t870-render-sync.patch
patch -p1 --forward < /path/to/wlroots-sm-t870-render-sync.patch
ninja -C build -j4
```

For a fresh tree, retain the existing build options before running Ninja:

```sh
meson setup build --buildtype=release \
  --prefix=/usr/local/lib/tabs7linux/wlroots --libdir=lib \
  -Dbackends=drm,libinput,x11 -Dexamples=false \
  -Dxwayland=enabled -Dsession=enabled -Dwrap_mode=nofallback
ninja -C build -j4
```

Check the built library's AArch64 machine, `libwlroots.so.12` SONAME, and
dynamic exports against the installed private library before installation.
Keep a checksum and backup of the old private library. Install only in the
private wlroots directory; the desktop launcher selects it for Phoc.

Host checks confirmed that this patch applies to the current private source
without fuzz and reverses back to byte-identical files. Native compilation
and source-patch hardware acceptance are separate checks from the successful
temporary interceptor A/B.

## Rollback and validation

Rollback uses the saved private library and its matching launcher setting.
The normal launcher both sets the completion variable and requires the marker.
For a diagnostic comparison, use a temporary launcher copy that removes both
the assignment and its matching marker check, or restore the saved old library
with its matching launcher. Unsetting an inherited variable alone has no effect.
Keep the two-plane profile enabled;
turning it off reintroduces the older narrow-strip display problem.

Validate the source-patched library without `LD_PRELOAD`, require its first
completion marker, and repeat app-list scrolling, overview transitions and
touch keyboard input. Recheck the unchanged mode, touch alignment, brightness,
power wake and volume controls. A 120 Hz scanout mode does not imply 120
rendered desktop frames per second; the completion wait can limit throughput.

## Native acceptance

The final native ARM64 library SHA256 is
`9c1bee19726702e8fb38bb5b66aeac520cc1794fbd6f305cf3cb604e1afb61fc`.
It retains the AArch64 machine, `libwlroots.so.12` SONAME and exactly the same
754 dynamic exports as the prior private library. After removing the diagnostic
interceptor, the user confirmed no flashing, with correct touch and keyboard.

The final reboot, boot ID `4100d7a9-da41-4987-b7e0-3df615af32cb`, verified the
actual loaded library inode/hash, enabled flag, one completed-wait marker and
absence of the temporary sync preload. Physical mode timing was 119.9998 Hz,
with a stable 120.0024 Hz driver vblank sample. The user then confirmed normal
scrolling, touch, screen keyboard, brightness and power off/wake without flashing.
Evidence is retained locally in ignored
`out/device/native-gnome/flicker-20261015/cold-final/`.
