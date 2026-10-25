"""Guards for the SteamOS hand-over helper in the native initramfs."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'boot/initramfs/start-persistent-gui'
INIT_VENDOR = ROOT / 'boot/initramfs/init-vendor-linux'
INIT_MAINLINE = ROOT / 'boot/initramfs/init'


class HandoverTest(unittest.TestCase):
    def setUp(self):
        self.text = HELPER.read_text()
        self.init_vendor = INIT_VENDOR.read_text()
        self.init_mainline = INIT_MAINLINE.read_text()

    def test_userdata_label_uses_partlabel_before_uevent(self):
        # Prefer partlabel where present; rev7 6.18 provides uevent PARTNAME.
        partlabel = self.text.index('partlabel')
        fallback = self.text.index('PARTNAME')
        self.assertLess(partlabel, fallback, 'partlabel must be tried first')
        self.assertIn('CONFIG_EFI_PARTITION', (ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/'
                                               'steamos-6.18.config').read_text())

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
        self.assertNotIn('start-persistent-gui select-steamos', self.init_vendor)

    def test_helper_hands_over_a_devtmpfs(self):
        self.assertIn('mount_new "$NEW_ROOT" -o bind "$steamos_root"', self.text)
        self.assertIn('-t devtmpfs', self.text)
        self.assertIn('exec /bin/busybox switch_root /newroot /usr/lib/systemd/systemd', self.init_vendor)

    def test_mainline_keeps_usb_up_and_waits_for_userdata(self):
        self.assertLess(self.init_mainline.index('muic_to_usb > /dev/kmsg'),
                        self.init_mainline.index('echo "$udc" > "$gadget/UDC"'))
        self.assertIn('while [ "$wait_count" -lt 50 ]', self.init_mainline)
        self.assertIn('SteamOS bootstrap failed; native USB shells remain active', self.init_mainline)
        self.assertNotIn('switch_root', self.init_mainline,
                         'BusyBox switch_root would delete the RAM files used by the outer shell')
        self.assertIn('unshare -m -p -f sh -c', self.init_mainline)
        self.assertIn('exec /bin/busybox chroot /newroot /usr/lib/systemd/systemd --unit=graphical.target',
                      self.init_mainline)
        self.assertIn('PrivateUsers=no', self.init_mainline)
        self.assertIn('ConditionVirtualization=', self.init_mainline)
        self.assertIn('SteamOS graphical target started in a namespace', self.init_mainline)
        self.assertIn('/bin/busybox unshare -m -p -f', self.init_mainline)
        self.assertIn('--unit=graphical.target', self.init_mainline)
        self.assertIn('mount --bind /sys /newroot/sys', self.init_mainline)
        self.assertIn('/etc/systemd/system/persist.mount', self.init_mainline)
        self.assertIn('/etc/systemd/system/tabs7-debug-shell.service', self.init_mainline)
        self.assertIn('ip route replace default via 192.168.77.1 dev usb0', self.init_mainline)

    def test_legacy_tree_is_adopted_by_rename_not_copy(self):
        self.assertIn('LEGACY_STEAMOS_RELATIVE=tabs7linux/ubuntu/steamos', self.text)
        self.assertIn('"$BB" mv "$legacy_root" "$steamos_root"', self.text)


if __name__ == '__main__':
    unittest.main()
