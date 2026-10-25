# Mainline panel status readback

Development-only diagnostic for the active SM-T870 master DSI panel. Build
against the exact running 6.18 kernel and check its exported symbol versions.
This uses the existing DSI host driver's DCS read path; it does not write panel
configuration, change clocks or reset the panel.

```sh
insmod tabs7_dsi_status.ko display_active=1
cat /sys/module/tabs7_dsi_status/parameters/power_mode
cat /sys/module/tabs7_dsi_status/parameters/read_error
dmesg | tail
rmmod tabs7_dsi_status
```

The power mode defaults to -1 and is populated only if the raw read returns
exactly one byte; a zero-length response is recorded as -ENODATA. A successful
transfer alone does not establish that the returned panel state is correct.
An error or zero-filled failed ID read must not be counted as a working panel.
The probe deliberately reads only the master device: bonded-DSI slave transfers
may be ignored by the host manager and cannot establish an independent readback.
