"""Synthetic sysfs only: ensure HCI software blocks cannot touch GPIO/Wi-Fi."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[3] / "userspace/platform/hci_rfkill.py"
SPEC = importlib.util.spec_from_file_location("hci_rfkill_helper", MODULE)
hci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hci)


class HciRfkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.device = self.root / hci.HCI_DEVICE
        self.device.mkdir(parents=True)
        (self.root / "class/bluetooth").mkdir(parents=True)
        (self.root / "class/bluetooth/hci0").symlink_to(self.device)
        (self.root / "class/rfkill").mkdir(parents=True)
        self.node = self.make_rfkill(self.device, "rfkill2", "hci0", "bluetooth")
        self.board = self.make_rfkill(self.root / "devices/board", "rfkill0", "bt_power", "bluetooth")
        self.wlan = self.make_rfkill(self.root / "devices/wifi", "rfkill1", "phy0", "wlan")

    def make_rfkill(self, parent, number, name, kind):
        path = parent / number
        path.mkdir(parents=True)
        for attr, value in dict(name=name, type=kind, soft="0", hard="0").items():
            (path / attr).write_text(value + "\n")
        (self.root / "class/rfkill" / number).symlink_to(path)
        return path

    def test_roundtrip_changes_hci_soft_only(self):
        before = {(node.name, attr): (node / attr).read_bytes()
                  for node in (self.board, self.wlan, self.node) for attr in ("soft", "hard", "name", "type")}
        self.assertTrue(hci.set_blocked(True, self.root)["soft"])
        self.assertEqual((self.node / "soft").read_text(), "1\n")
        for node in (self.board, self.wlan, self.node):
            for attr in ("soft", "hard", "name", "type"):
                if (node, attr) != (self.node, "soft"):
                    self.assertEqual((node / attr).read_bytes(), before[node.name, attr])
        self.assertFalse(hci.set_blocked(False, self.root)["soft"])
        self.assertEqual((self.node / "soft").read_text(), "0\n")

    def test_index_is_discovered_not_fixed(self):
        (self.root / "class/rfkill/rfkill2").unlink()
        self.node.rename(self.device / "rfkill9")
        (self.root / "class/rfkill/rfkill9").symlink_to(self.device / "rfkill9")
        self.assertEqual(Path(hci.set_blocked(True, self.root)["path"]).name, "rfkill9")

    def test_missing_hci_is_an_error(self):
        (self.root / "class/rfkill/rfkill2").unlink()
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            hci.read_state(self.root)

    def test_hci_on_wrong_uart_is_rejected(self):
        (self.root / "class/bluetooth/hci0").unlink()
        wrong = self.root / "devices/wrong-uart/hci0"
        wrong.mkdir(parents=True)
        (self.root / "class/bluetooth/hci0").symlink_to(wrong)
        with self.assertRaisesRegex(RuntimeError, "expected SM-T870"):
            hci.set_blocked(True, self.root)
        self.assertEqual((self.node / "soft").read_text(), "0\n")

    def test_hci_named_rfkill_cannot_resolve_to_board(self):
        (self.board / "name").write_text("hci0\n")
        with self.assertRaisesRegex(RuntimeError, "topology"):
            hci.set_blocked(True, self.root)
        self.assertEqual((self.board / "soft").read_text(), "0\n")

    def test_ambiguous_hci_nodes_are_rejected(self):
        self.make_rfkill(self.device, "rfkill3", "hci0", "bluetooth")
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            hci.set_blocked(True, self.root)

    def test_soft_symlink_cannot_target_board_gpio(self):
        (self.node / "soft").unlink()
        (self.node / "soft").symlink_to(self.board / "soft")
        with self.assertRaises(OSError):
            hci.set_blocked(True, self.root)
        self.assertEqual((self.board / "soft").read_text(), "0\n")

    def test_invalid_state_is_not_reported_unblocked(self):
        (self.node / "soft").write_text("unknown\n")
        with self.assertRaisesRegex(RuntimeError, "invalid HCI"):
            hci.read_state(self.root)
        with self.assertRaises(ValueError):
            hci.set_blocked("1", self.root)


if __name__ == "__main__":
    unittest.main()
