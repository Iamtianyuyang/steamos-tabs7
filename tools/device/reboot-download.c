/* Enter Download (ODIN) mode from native Linux. */
#define _GNU_SOURCE
#include <linux/reboot.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void)
{
    if (geteuid() != 0) {
        fputs("Download reboot requires root\n", stderr);
        return 1;
    }
    sync();
    syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
            LINUX_REBOOT_CMD_RESTART2, "download");
    perror("reboot to download");
    return 1;
}
