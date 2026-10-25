"""The SteamOS adaptation must be exactly reversible and match the initramfs marker."""
import importlib.util
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('adaptation', ROOT / 'scripts/steamos/install-systemd-adaptation.py')
adaptation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adaptation)


def snapshot(root):
    result = {}
    for path in sorted(root.rglob('*')):
        relative = str(path.relative_to(root))
        if relative.startswith(adaptation.STATE):
            continue
        if path.is_symlink():
            result[relative] = ('link', os.readlink(path))
        elif path.is_file():
            result[relative] = ('file', path.read_bytes())
    return result


class AdaptationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'usr/lib/systemd/system').mkdir(parents=True)
        (self.root / 'etc/systemd/system').mkdir(parents=True)
        (self.root / 'usr/lib/systemd/system/sddm.service').write_text('[Unit]\n')
        (self.root / 'etc/systemd/system/default.target').symlink_to('/usr/lib/systemd/system/graphical.target')
        (self.root / 'usr/lib/os-release').write_text('NAME="SteamOS"\nID=steamos\nVARIANT="VR"\nVARIANT_ID="vr"\n')
        (self.root / 'etc/passwd').write_text('root:x:0:0::/root:/bin/bash\nsteamos:x:1000:1000::/home/steamos:/bin/bash\n')
        (self.root / 'etc/group').write_text('root:x:0:\nseat:x:974:\nvideo:x:985:steamos\n')
        (self.root / 'etc/NetworkManager/conf.d').mkdir(parents=True)
        (self.root / 'etc/NetworkManager/conf.d/50-tabs7-usb0.conf').write_text('original\n')
        self.busybox = self.root.parent / (self.root.name + '-busybox')
        self.busybox.write_bytes(b'busybox')
        self.addCleanup(self.busybox.unlink)
        self.addCleanup(self.temp.cleanup)

    def install(self):
        with mock.patch.object(adaptation, 'BUSYBOX_SHA256', adaptation.sha(self.busybox)):
            return adaptation.install(adaptation.Tree(self.root), self.busybox)

    def test_install_then_uninstall_restores_the_tree(self):
        before = snapshot(self.root)
        absent = self.install()
        self.assertIn('rauc.service', absent)
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/sddm.service'), '/dev/null')
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/default.target'),
                         '/usr/lib/systemd/system/multi-user.target')
        self.assertTrue((self.root / 'etc/systemd/system/emergency.target.wants/tabs7-debug-shell.service').is_symlink())
        self.install()  # idempotent: originals are remembered only once
        adaptation.uninstall(adaptation.Tree(self.root))
        self.assertEqual(snapshot(self.root), before)

    def test_missing_shadow_is_created_locked(self):
        self.install()
        shadow = self.root / 'etc/shadow'
        self.assertEqual(shadow.stat().st_mode & 0o777, 0o600)
        self.assertEqual([line.split(':')[:2] for line in shadow.read_text().splitlines()],
                         [['root', '!*'], ['steamos', '!*']])
        self.assertIn('video:!*::steamos', (self.root / 'etc/gshadow').read_text())

    def test_marker_matches_the_initramfs_selector(self):
        self.install()
        script = (ROOT / 'boot/initramfs/start-persistent-gui').read_text()
        expected = re.search(r"STEAMOS_MARKER='([^']*)'", script)[1]
        self.assertEqual((self.root / 'etc/tabs7linux/steamos-adaptation').read_text(), expected)

    def test_symlinked_parent_is_refused(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / 'etc/systemd/journald.conf.d').symlink_to(outside.name)
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(list(Path(outside.name).iterdir()), [])

    def firmware(self, corrupt=None):
        directory = Path(self.temp.name + '-fw')
        directory.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in directory.iterdir()] and None or directory.rmdir())
        records = []
        for name in sorted(adaptation.GPU_FIRMWARE):
            data = name.encode()
            (directory / name).write_bytes(data)
            records.append({'path': name, 'size': len(data),
                            'sha256': adaptation.hashlib.sha256(data).hexdigest()})
        if corrupt:
            (directory / corrupt).write_bytes(b'X' * len(corrupt))
        manifest = directory / 'gpu-firmware.json'
        manifest.write_text(adaptation.json.dumps({'firmware': records}))
        return adaptation.gpu_firmware(manifest, directory)

    def test_game_mode_layer_installs_and_reverts(self):
        before = snapshot(self.root)
        with mock.patch.object(adaptation, 'BUSYBOX_SHA256', adaptation.sha(self.busybox)):
            adaptation.install(adaptation.Tree(self.root), self.busybox, self.firmware())
        self.assertEqual(os.readlink(self.root / 'etc/systemd/system/default.target'),
                         '/usr/lib/systemd/system/graphical.target')
        self.assertTrue((self.root / 'etc/systemd/system/graphical.target.wants/tabs7-gamescope-session.service').is_symlink())
        self.assertEqual((self.root / 'usr/lib/firmware/a650_sqe.fw').read_bytes(), b'a650_sqe.fw')
        self.assertTrue(os.access(self.root / 'usr/local/lib/tabs7linux/steamos/gamescope-session', os.X_OK))
        # Power button: tagged for steamos-powerbuttond, which runs in the user session.
        self.assertIn('ENV{STEAMOS_POWER_BUTTON}="1"',
                      (self.root / 'etc/udev/rules.d/69-tabs7-power-button.rules').read_text())
        self.assertIn('org.freedesktop.login1.suspend',
                      (self.root / 'etc/polkit-1/rules.d/50-tabs7-steamos-suspend.rules').read_text())
        self.assertEqual(os.readlink(self.root / 'etc/systemd/user/default.target.wants/steamos-powerbuttond.service'),
                         '/etc/systemd/user/steamos-powerbuttond.service')
        self.assertEqual((self.root / 'usr/local/lib/tabs7linux/steamos/os-release').read_text(),
                         'NAME="SteamOS"\nID=steamos\n')
        adaptation.uninstall(adaptation.Tree(self.root))
        self.assertEqual(snapshot(self.root), before)

    def test_game_mode_steam_handles_volume_keys(self):
        session = adaptation.SESSION.read_text()
        environment = re.search(r'^steam_env=\(\n(.*?)^\)', session, re.M | re.S)
        self.assertIsNotNone(environment)
        self.assertRegex(environment.group(1),
                         r'(?m)^\s*STEAM_ENABLE_VOLUME_HANDLER=1\s*$')

    def test_corrupt_gpu_firmware_is_refused(self):
        with self.assertRaises(ValueError):
            self.firmware(corrupt='a650_zap.b01')

    def test_wrong_busybox_is_refused(self):
        with self.assertRaises(ValueError):
            adaptation.install(adaptation.Tree(self.root), self.busybox)


if __name__ == '__main__':
    unittest.main()


class PowerButtonTest(unittest.TestCase):
    def test_rule_matches_the_kernel_power_key(self):
        driver = (ROOT / 'linux-sm8250-v6.18/drivers/input/misc/pm8941-pwrkey.c').read_text()
        rule = (adaptation.SOURCE / '69-tabs7-power-button.rules').read_text()
        name = re.search(r'ATTRS\{name\}=="([^"]+)"', rule).group(1)
        self.assertIn(f'"{name}"', driver)
        # Must be set before 70-steamos-power-button.rules grants seat access.
        self.assertLess('69-tabs7-power-button.rules', '70-steamos-power-button.rules')

    def test_powerbuttond_override_drops_the_frame_gates(self):
        unit = (adaptation.SOURCE / 'steamos-powerbuttond.service').read_text()
        directives = [line.split('=')[0] for line in unit.splitlines() if '=' in line and not line.startswith('#')]
        self.assertNotIn('Requisite', directives)
        self.assertNotIn('ExecCondition', directives)
        self.assertIn('ExecStart=/usr/lib/hwsupport/steamos-powerbuttond', unit)
        self.assertIn('WantedBy=default.target', unit)

    def test_polkit_grants_only_suspend(self):
        rule = (adaptation.SOURCE / '50-tabs7-steamos-suspend.rules').read_text()
        actions = set(re.findall(r'"(org\.freedesktop\.login1\.[a-z-]+)"', rule))
        self.assertEqual(actions, {'org.freedesktop.login1.suspend',
                                   'org.freedesktop.login1.suspend-multiple-sessions'})
        self.assertIn('subject.user == "steamos"', rule)
