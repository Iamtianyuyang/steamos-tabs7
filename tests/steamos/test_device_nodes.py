"""The node service must decide correctly without root or a real device."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'create_missing_nodes', ROOT / 'userspace/steamos/device-nodes/create-missing-nodes.py')
nodes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nodes)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sysfs = root / 'sys'
        self.dev = root / 'dev'
        self.sysfs.mkdir()
        self.dev.mkdir()

    def event(self, name, major, minor):
        entry = self.sysfs / name
        entry.mkdir()
        (entry / 'dev').write_text(f'{major}:{minor}\n')
        return entry

    def test_missing_node_is_planned(self):
        self.event('event3', 13, 67)
        creates, stale = nodes.plan(self.sysfs, self.dev)
        self.assertEqual([(node.name, rdev) for node, rdev, _ in creates], [('event3', os.makedev(13, 67))])
        self.assertEqual(stale, [])

    def test_existing_node_with_wrong_rdev_is_recreated(self):
        # A regular file has st_rdev == 0, so it must be replaced by a device node.
        self.event('event3', 13, 67)
        (self.dev / 'event3').write_text('')
        creates, stale = nodes.plan(self.sysfs, self.dev)
        self.assertEqual([(node.name, rdev) for node, rdev, _ in creates], [('event3', os.makedev(13, 67))])
        self.assertEqual(stale, [])

    def test_stale_node_is_reported(self):
        (self.dev / 'event9').write_text('')
        creates, stale = nodes.plan(self.sysfs, self.dev)
        self.assertEqual(creates, [])
        self.assertEqual([node.name for node in stale], ['event9'])

    def test_sysfs_entry_without_dev_file_is_ignored(self):
        (self.sysfs / 'event4').mkdir()
        creates, stale = nodes.plan(self.sysfs, self.dev)
        self.assertEqual((creates, stale), ([], []))

    def test_service_unit_matches_the_script_path(self):
        unit = (ROOT / 'userspace/steamos/systemd/tabs7-device-nodes.service').read_text()
        self.assertIn('/usr/local/lib/tabs7linux/steamos/create-missing-nodes.py', unit)
        self.assertIn('WantedBy=sysinit.target', unit)
        installer = (ROOT / 'scripts/steamos/install-systemd-adaptation.py').read_text()
        self.assertIn("device-nodes/create-missing-nodes.py", installer)
        self.assertIn('sysinit.target.wants/tabs7-device-nodes.service', installer)


if __name__ == '__main__':
    unittest.main()
