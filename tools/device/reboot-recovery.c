/* Enter Recovery from native Linux after the caller unmounts persistent data. */
#define _GNU_SOURCE
#include <linux/reboot.h>
#include <stdio.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void)
{
    if (geteuid() != 0) {
        fputs("Recovery reboot requires root\n", stderr);
        return 1;
    }
    sync();
    syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
            LINUX_REBOOT_CMD_RESTART2, "recovery");
    perror("reboot to recovery");
    return 1;
}
