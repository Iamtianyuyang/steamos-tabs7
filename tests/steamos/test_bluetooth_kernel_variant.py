"""The IPC kernel must select its rebuilt module without losing stock rollback."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import unittest

spec = importlib.util.spec_from_file_location('bluetooth_variant', Path(__file__).resolve().parents[2] / 'userspace/platform/start-bluetooth.py')
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)


class BluetoothKernelVariantTests(unittest.TestCase):
    def guard(self, config_sha, paths):
        fake_path = mock.Mock()
        fake_path.return_value.read_bytes.return_value = b'configuration'
        fake_path.return_value.read_text.return_value = '0 t hci_sock_ops\n0 t hci_sock_bind\n0 t hci_sock_destruct\n'
        with mock.patch.object(bt, 'Path', fake_path), mock.patch.object(bt.gzip, 'decompress', return_value=b'config'), mock.patch.object(bt, 'digest', return_value=config_sha), mock.patch.object(bt.os, 'uname', return_value=SimpleNamespace(release=bt.RELEASE, machine='aarch64')):
            bt.kernel_guard(paths)

    def test_ipc_and_original_kernels_select_matching_modules(self):
        paths = dict(bt.DEFAULTS)
        self.guard(bt.STEAMOS_IPC_CONFIG_SHA, paths)
        self.assertTrue(paths['module'].endswith('tabs7_hci_uart-steamos-ipc.ko'))
        self.assertEqual(bt.HASHES['module'], bt.STEAMOS_IPC_MODULE_SHA)
        paths = dict(bt.DEFAULTS)
        self.guard(bt.CONFIG_SHA, paths)
        self.assertEqual(paths['module'], bt.DEFAULTS['module'])
        self.assertEqual(bt.HASHES['module'], 'c70a7c1105789be7f3679ec149108312d94d016dd2ff5d298cb8e8e0e9a5aef8')

    def test_systemd_kernel_selects_its_own_rebuilt_module(self):
        paths = dict(bt.DEFAULTS)
        self.guard(bt.STEAMOS_SYSTEMD_CONFIG_SHA, paths)
        self.assertTrue(paths['module'].endswith('tabs7_hci_uart-steamos-systemd.ko'))
        self.assertEqual(bt.HASHES['module'], bt.STEAMOS_SYSTEMD_MODULE_SHA)
        self.assertNotEqual(bt.STEAMOS_SYSTEMD_MODULE_SHA, bt.STEAMOS_IPC_MODULE_SHA)

    def test_unknown_kernel_configuration_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.guard('unreviewed', dict(bt.DEFAULTS))


if __name__ == '__main__':
    unittest.main()
