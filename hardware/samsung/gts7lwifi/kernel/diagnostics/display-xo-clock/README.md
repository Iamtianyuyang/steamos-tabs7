# Display XO clock comparison

Development-only probe for the observed difference between the working vendor
kernel (GCC_DISP_XO_CLK enabled) and mainline (disabled). Build against the exact
running kernel and verify imported symbol versions before loading.

Loading adds one balanced Common Clock Framework prepare/enable vote for
GCC_DISP_XO_CLK. It does not write raw MMIO, change a clock rate, issue a reset,
or alter the panel sequence. Unloading releases that vote. There is no automatic
service or installation step. This experiment is not evidence that the clock
is the cause of a display failure.

```sh
insmod tabs7_disp_xo.ko
# Capture display state and repeat the original modeset/display check.
rmmod tabs7_disp_xo
```
