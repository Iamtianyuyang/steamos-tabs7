"""Guards for the SteamOS hand-over helper in the native initramfs."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'boot/initramfs/start-persistent-gui'
INIT = ROOT / 'boot/initramfs/init-vendor-linux'


class HandoverTest(unittest.TestCase):
    def setUp(self):
        self.text = HELPER.read_text()
        self.init = INIT.read_text()

    def test_userdata_label_uses_partlabel_before_uevent(self):
        # 6.2 exposes the GPT name as partlabel; 4.19 only as uevent PARTNAME.
        partlabel = self.text.index('partlabel')
        fallback = self.text.index('PARTNAME')
        self.assertLess(partlabel, fallback, 'partlabel must be tried first')
        self.assertIn('CONFIG_EFI_PARTITION', (ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/'
                                               'steamos-6.2.config').read_text())

    def test_userdata_is_still_verified_by_content(self):
        # The label only finds the device; the F2FS magic and UUID decide.
        self.assertIn('superblock_offset in 1024 5120', self.text)
        self.assertIn('1020f5f2', self.text)
        self.assertIn('DATA_UUID_HEX=c53f40a0a13940d3bac7333aae100724', self.text)
        self.assertIn('expected one userdata partition', self.text)

    def test_no_ubuntu_exit_remains(self):
        for forbidden in ('start-xfce', 'chroot', 'boot-target', 'steamos-once',
                          'TABS7LINUX_ROOTFS', 'build-epoch', 'trace-display'):
            self.assertNotIn(forbidden, self.text, forbidden)
        self.assertNotIn('start-persistent-gui select-steamos', self.init)

    def test_helper_hands_over_a_devtmpfs(self):
        self.assertIn('mount_new "$NEW_ROOT" -o bind "$steamos_root"', self.text)
        self.assertIn('-t devtmpfs', self.text)
        self.assertIn('exec /bin/busybox switch_root /newroot /usr/lib/systemd/systemd', self.init)

    def test_legacy_tree_is_adopted_by_rename_not_copy(self):
        self.assertIn('LEGACY_STEAMOS_RELATIVE=tabs7linux/ubuntu/steamos', self.text)
        self.assertIn('"$BB" mv "$legacy_root" "$steamos_root"', self.text)


if __name__ == '__main__':
    unittest.main()
