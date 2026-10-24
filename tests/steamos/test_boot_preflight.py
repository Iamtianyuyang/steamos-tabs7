"""Offline checks of the SteamOS BOOT preflight (no device, no avbtool)."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'boot_preflight', ROOT / 'scripts/build/vendor/preflight-steamos-boot.py')
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

def sha(data):
    return hashlib.sha256(data).hexdigest()


LAYOUT = {'dtbo': sha(b'd' * 8), 'recovery': sha(b'e' * 8)}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.snapshot = Path(self.temp.name) / 'snap'
        self.profile = {'device_pit': {'partitions': {
            name.upper(): {'capacity_bytes': 8} for name in preflight.PARTITIONS}}}
        self.images = {'boot': b'b' * 8, 'dtbo': b'd' * 8, 'recovery': b'e' * 8, 'vbmeta': b'v' * 8}
        self.manifest = {
            'boot_id': 'boot-id', 'partitions': {},
            'qcom,board-id': preflight.EXPECTED_BOARD_ID, 'qcom,msm-id': preflight.EXPECTED_MSM_ID,
            'live.dtb': {'size': 4, 'sha256': sha(b'dtb!')},
        }
        for name, data in self.images.items():
            write(self.snapshot / f'{name}.img', data)
            self.manifest['partitions'][name] = {'sha256': sha(data), 'bytes': len(data)}
        write(self.snapshot / 'live.dtb', b'dtb!')
        self.save()

    def save(self):
        (self.snapshot / 'manifest.json').write_text(json.dumps(self.manifest))

    def test_consistent_snapshot_is_accepted(self):
        checked = preflight.verify_snapshot(self.snapshot, self.manifest, self.profile, LAYOUT)
        self.assertEqual(checked['boot_id'], 'boot-id')
        self.assertEqual(checked['paths']['boot'], self.snapshot / 'boot.img')

    def test_partition_hash_mismatch_is_refused(self):
        write(self.snapshot / 'boot.img', b'X' * 8)
        with self.assertRaisesRegex(ValueError, 'does not match its manifest hash'):
            preflight.verify_snapshot(self.snapshot, self.manifest, self.profile, LAYOUT)

    def test_incomplete_partition_read_is_refused(self):
        write(self.snapshot / 'vbmeta.img', b'v' * 4)
        self.manifest['partitions']['vbmeta']['sha256'] = sha(b'v' * 4)
        self.save()
        with self.assertRaisesRegex(ValueError, 'not a complete partition read'):
            preflight.verify_snapshot(self.snapshot, self.manifest, self.profile, LAYOUT)

    def test_wrong_layout_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'expected SteamOS migration layout'):
            preflight.verify_snapshot(self.snapshot, self.manifest, self.profile,
                                      {'dtbo': sha(b'd' * 8), 'recovery': sha(b'E' * 8)})

    def test_changed_live_dtb_is_refused(self):
        write(self.snapshot / 'live.dtb', b'other')
        with self.assertRaisesRegex(ValueError, 'live DTB'):
            preflight.verify_snapshot(self.snapshot, self.manifest, self.profile, LAYOUT)

    def test_wrong_hardware_identity_is_refused(self):
        for key, value, message in (('qcom,board-id', [8, 6], 'board-id'),
                                    ('qcom,msm-id', [356, 0x10001], 'msm-id')):
            with self.subTest(key):
                saved = self.manifest[key]
                self.manifest[key] = value
                self.save()
                with self.assertRaisesRegex(ValueError, message):
                    preflight.verify_snapshot(self.snapshot, self.manifest, self.profile, LAYOUT)
                self.manifest[key] = saved
                self.save()

    def test_find_snapshot_requires_a_unique_candidate(self):
        original = preflight.SNAPSHOT_ROOTS
        preflight.SNAPSHOT_ROOTS = (self.snapshot.parent,)
        self.addCleanup(setattr, preflight, 'SNAPSHOT_ROOTS', original)
        self.assertEqual(preflight.find_snapshot(LAYOUT), self.snapshot)
        shutil.copytree(self.snapshot, self.snapshot.parent / 'snap-copy')
        with self.assertRaisesRegex(ValueError, 'pass --snapshot explicitly'):
            preflight.find_snapshot(LAYOUT)

    def test_find_snapshot_skips_unreadable_and_foreign_directories(self):
        original = preflight.SNAPSHOT_ROOTS
        preflight.SNAPSHOT_ROOTS = (self.snapshot.parent,)
        self.addCleanup(setattr, preflight, 'SNAPSHOT_ROOTS', original)
        (self.snapshot.parent / 'not-a-snapshot').mkdir()
        (self.snapshot.parent / 'stray.json').write_text('{}')
        self.assertEqual(preflight.find_snapshot(LAYOUT), self.snapshot)


class BackupViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = {name: write(self.root / 'snapshot' / f'{name}.img', name.encode() * 4)
                      for name in preflight.PARTITIONS}
        self.deployed = write(self.root / 'deployed-boot.img', b'deployed')

    def test_boot_of_the_view_is_the_deployed_image(self):
        destination = self.root / 'view'
        evidence = preflight.assemble_backup_view(self.paths, self.deployed, {'boot_id': 'id'}, destination)
        self.assertEqual((destination / 'boot.img').read_bytes(), b'deployed')
        for name in ('dtbo', 'recovery', 'vbmeta'):
            self.assertEqual((destination / f'{name}.img').read_bytes(), self.paths[name].read_bytes())
        self.assertEqual(evidence['partitions']['boot']['sha256'], sha(b'deployed'))
        self.assertEqual(evidence['boot_id'], 'id')
        self.assertEqual(json.loads((destination / 'manifest.json').read_text())['partitions'],
                         evidence['partitions'])

    def test_existing_view_is_not_overwritten(self):
        (self.root / 'view').mkdir()
        with self.assertRaises(FileExistsError):
            preflight.assemble_backup_view(self.paths, self.deployed, {'boot_id': 'id'}, self.root / 'view')

    def test_output_outside_out_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'under the project out/ directory'):
            preflight.ensure_under_out(self.root / 'anywhere')
        self.assertTrue(preflight.ensure_under_out(ROOT / 'out/preflight-test'))


if __name__ == '__main__':
    unittest.main()
