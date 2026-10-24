"""Flashing policy must not let the Steam IPC change admit unrelated options."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('repack', Path(__file__).resolve().parents[2] / 'scripts/build/vendor/repack-vendor-kernel.py')
repack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repack)
BASE = b'# CONFIG_SYSVIPC is not set\nCONFIG_CFI_CLANG=y\n# CONFIG_DEBUG_FS is not set\n'
IPC = b'CONFIG_SYSVIPC=y\nCONFIG_SYSVIPC_COMPAT=y\nCONFIG_SYSVIPC_SYSCTL=y\n# CONFIG_IPC_NS is not set\nCONFIG_CFI_CLANG=y\n# CONFIG_DEBUG_FS is not set\n'


class KernelConfigPolicyTests(unittest.TestCase):
    def test_default_policy_still_rejects_ipc_configuration(self):
        with self.assertRaises(ValueError):
            repack.reviewed_config(BASE, IPC, 'original')
        self.assertEqual(repack.reviewed_config(BASE, BASE, 'original'), {})

    def test_explicit_policy_accepts_only_reviewed_ipc_delta(self):
        self.assertEqual(set(repack.reviewed_config(BASE, IPC, 'steamos-ipc')),
                         {'CONFIG_SYSVIPC', 'CONFIG_SYSVIPC_COMPAT', 'CONFIG_SYSVIPC_SYSCTL', 'CONFIG_IPC_NS'})
        for candidate in [IPC.replace(b'CONFIG_CFI_CLANG=y', b'# CONFIG_CFI_CLANG is not set'),
                          IPC.replace(b'# CONFIG_DEBUG_FS is not set', b'CONFIG_DEBUG_FS=y'),
                          IPC.replace(b'# CONFIG_IPC_NS is not set', b'CONFIG_IPC_NS=y'),
                          IPC.replace(b'CONFIG_SYSVIPC_COMPAT=y\n', b'')]:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                repack.reviewed_config(BASE, candidate, 'steamos-ipc')

    def test_systemd_policy_requires_the_whole_reviewed_delta(self):
        base = BASE + b''.join(b'# CONFIG_%s is not set\n' % name for name in
                               (b'AUTOFS_FS', b'CGROUP_PIDS', b'DEVTMPFS', b'FHANDLE', b'PID_NS', b'USER_NS'))
        systemd = (IPC + b'CONFIG_AUTOFS_FS=y\nCONFIG_CGROUP_PIDS=y\nCONFIG_DEVTMPFS=y\n'
                   b'# CONFIG_DEVTMPFS_MOUNT is not set\nCONFIG_FHANDLE=y\nCONFIG_PID_NS=y\nCONFIG_USER_NS=y\n')
        self.assertEqual(repack.reviewed_config(base, systemd, 'steamos-systemd'),
                         repack.STEAMOS_SYSTEMD_CHANGES)
        for candidate in [systemd.replace(b'CONFIG_USER_NS=y\n', b'# CONFIG_USER_NS is not set\n'),
                          systemd.replace(b'# CONFIG_DEVTMPFS_MOUNT is not set', b'CONFIG_DEVTMPFS_MOUNT=y'),
                          systemd.replace(b'# CONFIG_IPC_NS is not set', b'CONFIG_IPC_NS=y'),
                          systemd + b'CONFIG_DEBUG_FS=y\n']:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                repack.reviewed_config(base, candidate, 'steamos-systemd')
        # Neither narrower policy admits the systemd delta.
        for policy in ('original', 'steamos-ipc'):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                repack.reviewed_config(base, systemd, policy)


if __name__ == '__main__':
    unittest.main()
