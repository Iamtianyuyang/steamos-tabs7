# Active display register comparison

This development probe captures fixed SM8250 display/clock MMIO ranges once,
without writing registers, enabling clocks, changing power or requesting resets.
Use only while the display hardware is powered. It is not a production driver
and is not installed as an automatic service.

Build against the exact running kernel's prepared source, configuration and
Module.symvers. Verify configuration, release and imported symbol CRCs before
loading. For paths containing spaces, copy these two build inputs into the
project's Kbuild cache first.

```sh
insmod tabs7_display_snapshot.ko display_active=1
cat /sys/kernel/tabs7_display_snapshot/registers > display-registers.bin
rmmod tabs7_display_snapshot
```

The sysfs file is an immutable in-memory capture, readable only by root. Format
is little endian: three u32 fields (magic `0x37534254`, version 1, pair count),
followed by that many u32 address/value pairs. The address is physical, so the
DSI driver's additional MMIO base offset cannot shift comparisons between
vendor 4.19 and mainline 6.18. Capture metadata (kernel release, configuration
hash, display mode and whether an image was actually visible) separately.

The ranges come from the SM8250 DTS, DPU catalog and clock driver. This probe
deliberately does not read framebuffer contents or arbitrary physical memory.
