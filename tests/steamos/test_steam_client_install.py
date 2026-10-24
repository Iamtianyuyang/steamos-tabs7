import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import zipfile

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = REPOSITORY / 'scripts/steamos/extract-steam-arm64.py'
spec = importlib.util.spec_from_file_location('steam_extract', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
ELF = b'\x7fELF\x02\x01' + bytes(12) + (183).to_bytes(2, 'little')


def make_archive(path, extra=(), executable=ELF):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('steamrtarm64/steam', executable)
        for name, content, symlink in extra:
            entry = zipfile.ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16 if symlink else 0
            archive.writestr(entry, content)


class SteamExtractionTests(unittest.TestCase):
    def test_separate_matching_cef_package_is_merged_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            primary = base / 'client.zip'
            cef = base / 'cef.zip'
            make_archive(primary)
            with zipfile.ZipFile(cef, 'w') as archive:
                archive.writestr('steamrtarm64/libcef.so', b'cef runtime')
            module.install(primary, base / 'Steam', (cef,))
            self.assertEqual((base / 'Steam/steamrtarm64/libcef.so').read_bytes(), b'cef runtime')

    def test_backslash_library_link_is_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / 'client.zip'
            make_archive(archive, [
                ('steamrtarm64/libs/libcurl.so.4', b'library', False),
                (r'steamrtarm64\libs\libcurl.so', b'libcurl.so.4', True),
            ])
            destination = base / 'Steam'
            module.install(archive, destination)
            link = destination / 'steamrtarm64/libs/libcurl.so'
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_bytes(), b'library')
            self.assertTrue(os.access(destination / 'steamrtarm64/steam', os.X_OK))
            self.assertFalse(any('\\' in path.name for path in destination.rglob('*')))

    def test_update_preserves_old_runtime_and_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            destination = base / 'Steam'
            runtime = destination / 'steamrtarm64'
            runtime.mkdir(parents=True)
            (runtime / 'old').write_bytes(b'previous runtime')
            (destination / 'private-config').write_bytes(b'user settings')
            archive = base / 'client.zip'
            make_archive(archive)
            backup = module.install(archive, destination)
            self.assertEqual((backup / 'old').read_bytes(), b'previous runtime')
            self.assertEqual((destination / 'private-config').read_bytes(), b'user settings')

    def test_wrong_architecture_leaves_existing_runtime_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            runtime = base / 'Steam/steamrtarm64'
            runtime.mkdir(parents=True)
            (runtime / 'old').write_bytes(b'previous runtime')
            archive = base / 'client.zip'
            make_archive(archive, executable=b'not ARM64')
            with self.assertRaisesRegex(ValueError, 'AArch64'):
                module.install(archive, runtime.parent)
            self.assertEqual((runtime / 'old').read_bytes(), b'previous runtime')

    def test_path_escape_and_link_escape_are_rejected_before_install(self):
        for name, content, link in [
            ('steamrtarm64/../../outside', b'bad', False),
            (r'steamrtarm64\libs\bad', b'../../../outside', True),
        ]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                archive = base / 'client.zip'
                make_archive(archive, [(name, content, link)])
                with self.assertRaises(ValueError):
                    module.install(archive, base / 'Steam')
                self.assertFalse((base / 'Steam').exists())
                self.assertFalse((base / 'outside').exists())

    @unittest.skipUnless(os.getuid() in (0, 1000) and (os.getuid() == 0 or 1000 in os.getgroups()),
                         'installer sets target uid/gid 1000')
    def test_installer_keeps_existing_registry_and_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            steam_home = base / 'home'
            root = steam_home / '.local/share/Steam'
            (root / 'config').mkdir(parents=True)
            (steam_home / '.steam').mkdir()
            config = root / 'config/config.vdf'
            registry = steam_home / '.steam/registry.vdf'
            config.write_bytes(b'existing configuration')
            registry.write_bytes(b'existing registry')
            # The official manifest contains distinct payloads in these paths.
            # An old alias lets a successful update corrupt the native runtime.
            (root / 'steamrt64').symlink_to('steamrtarm64')
            (root / 'linuxarm64').mkdir()
            (root / 'linuxarm64/user-marker').write_bytes(b'keep real runtime')
            archive = base / 'client.zip'
            make_archive(archive)
            subprocess.run(['sh', str(SOURCE.with_name('install-steam-arm64.sh')),
                            str(archive), str(steam_home)], check=True, capture_output=True)
            self.assertEqual(config.read_bytes(), b'existing configuration')
            self.assertEqual(registry.read_bytes(), b'existing registry')
            self.assertFalse((root / 'steam.cfg').exists(), 'must not disable updater/checksums')
            self.assertFalse((root / 'steamrt64').is_symlink())
            self.assertFalse((root / 'steamrt64').exists())
            self.assertEqual((root / 'linuxarm64/user-marker').read_bytes(), b'keep real runtime')


if __name__ == '__main__':
    unittest.main()
