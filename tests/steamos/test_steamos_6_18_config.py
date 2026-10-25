"""The 6.18 SteamOS configuration layer must keep the 4.19 decisions."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/gts7lwifi-bringup.config'
STEAMOS = ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/steamos-6.18.config'


def settings(path):
    """Return {symbol: value} where 'not set' is recorded explicitly."""
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith('# CONFIG_'):
            result[line.split()[1]] = 'not set'
        elif line.startswith('CONFIG_'):
            symbol, _, value = line.partition('=')
            result[symbol] = value
    return result


class SteamOSConfigTest(unittest.TestCase):
    def setUp(self):
        self.base = settings(BASE)
        self.steamos = settings(STEAMOS)

    def test_systemd_requirements_are_requested(self):
        required = ['CONFIG_FHANDLE=y', 'CONFIG_FUTEX=y', 'CONFIG_CGROUPS=y', 'CONFIG_CGROUP_PIDS=y',
                    'CONFIG_NAMESPACES=y', 'CONFIG_USER_NS=y', 'CONFIG_PID_NS=y',
                    'CONFIG_AUTOFS_FS=y', 'CONFIG_EPOLL=y', 'CONFIG_SIGNALFD=y',
                    'CONFIG_TIMERFD=y', 'CONFIG_EVENTFD=y', 'CONFIG_AIO=y',
                    'CONFIG_IO_URING=y', 'CONFIG_INOTIFY_USER=y', 'CONFIG_FANOTIFY=y',
                    'CONFIG_IPV6=y', 'CONFIG_FILE_LOCKING=y', 'CONFIG_BINFMT_MISC=y',
                    'CONFIG_POSIX_MQUEUE=y', 'CONFIG_SECCOMP=y', 'CONFIG_SECCOMP_FILTER=y',
                    'CONFIG_BPF_SYSCALL=y']
        for entry in required:
            symbol, _, value = entry.partition('=')
            self.assertEqual(self.steamos.get(symbol), value, entry)

    def test_system_v_ipc_matches_the_vendor_variant(self):
        for symbol in ('CONFIG_SYSVIPC', 'CONFIG_SYSVIPC_SYSCTL', 'CONFIG_SYSVIPC_COMPAT'):
            self.assertEqual(self.steamos.get(symbol), 'y', symbol)
        # The vendor 4.19 variant kept IPC namespaces off; do not flip silently.
        self.assertEqual(self.steamos.get('CONFIG_IPC_NS'), 'not set')

    def test_userdata_filesystem_is_built_in(self):
        self.assertEqual(self.steamos.get('CONFIG_F2FS_FS'), 'y')
        self.assertEqual(self.steamos.get('CONFIG_QUOTA'), 'y',
                         'the on-device F2FS quota feature requires CONFIG_QUOTA for RW mounts')

    def test_devtmpfs_is_not_mounted_by_the_kernel(self):
        self.assertEqual(self.base.get('CONFIG_DEVTMPFS'), 'y')
        self.assertEqual(self.steamos.get('CONFIG_DEVTMPFS_MOUNT'), 'not set',
                         'the initramfs builds devtmpfs itself; the kernel must not race it')

    def test_layers_do_not_disagree_outside_the_reviewed_overrides(self):
        allowed = {'CONFIG_DEVTMPFS_MOUNT'}
        conflicts = {symbol for symbol in self.base
                     if symbol in self.steamos and self.base[symbol] != self.steamos[symbol]}
        self.assertEqual(conflicts - allowed, set())

    def test_no_inline_comments(self):
        # KCONFIG_ALLCONFIG does not strip inline comments: "CONFIG_X=8  # note"
        # made NR_CPUS invalid during the first build of this fragment.
        for line in STEAMOS.read_text().splitlines():
            if line.startswith('CONFIG_'):
                self.assertNotIn('#', line, line)


if __name__ == '__main__':
    unittest.main()
