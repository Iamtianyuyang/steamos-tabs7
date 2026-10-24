"""Exercise real filesystem tools; target executables are never run."""
import contextlib
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/steamos/inspect-holo-rootfs.py"
spec = importlib.util.spec_from_file_location("inspect_holo", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class InspectionTests(unittest.TestCase):
    def test_unsupported_image_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.img"
            path.write_bytes(b"not a filesystem")
            result = subprocess.run(["python3", str(SOURCE), str(path)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported filesystem", result.stderr)
            self.assertNotIn("MISSING", result.stdout)

    def exercise_image(self, kind, builder, reader):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            tree = base / "source"
            for item, text in {
                "usr/lib/os-release": 'ID=steamos\nVERSION_ID=fixture\n',
                "etc/os-release": 'ID=steamos\nVERSION_ID=fixture\n',
                "etc/passwd": 'steamos:x:1000:1000::/home/steamos:/bin/bash\n',
                "usr/lib/libc.so.6": 'GNU C Library release version 2.39.\n',
                "usr/bin/gamescope": 'fixture; never execute this file\n',
                "var/lib/pacman/local/gamescope-1-1/desc":
                    '%NAME%\ngamescope\n\n%VERSION%\n1-1\n\n%ARCH%\naarch64\n',
            }.items():
                target = tree / item
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            image = base / "filesystem.img"
            with image.open("wb") as handle:
                handle.truncate(128 * 1024 * 1024)
            builder(image, tree)
            before = digest(image)
            self.assertEqual(module.filesystem_type(image), kind)
            temporary = base / "inspection"
            temporary.mkdir()
            inspected = reader(image, temporary)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                module.inspect(inspected, temporary)
            text = output.getvalue()
            self.assertIn('ID=steamos', text)
            self.assertIn('steamos uid=1000', text)
            self.assertIn('readable database entries: 1', text)
            self.assertIn('gamescope 1-1 aarch64', text)
            self.assertIn('\n  2.39\n', text)
            self.assertEqual(digest(image), before, "inspection modified the source image")

    @unittest.skipUnless(shutil.which("mkfs.ext4") and shutil.which("debugfs"), "e2fsprogs unavailable")
    def test_ext4_real_image(self):
        self.exercise_image("ext4",
            lambda image, tree: subprocess.run(
                ["mkfs.ext4", "-q", "-F", "-d", str(tree), str(image)], check=True),
            lambda image, temporary: module.Image(image))

    def test_btrfs_real_image(self):
        tool = os.environ.get("TABS7_BTRFS_TOOL", shutil.which("btrfs") or "")
        mkfs = str(Path(tool).with_name("mkfs.btrfs")) if tool else ""
        if tool and not Path(mkfs).is_file():
            mkfs = str(Path(tool).parent.parent / "sbin/mkfs.btrfs")
        if not tool or not Path(mkfs).is_file():
            self.skipTest("btrfs-progs unavailable; set TABS7_BTRFS_TOOL")
        self.exercise_image("btrfs",
            lambda image, tree: subprocess.run(
                [mkfs, "-q", "-f", "-r", str(tree), str(image)], check=True,
                capture_output=True),
            lambda image, temporary: module.BtrfsImage(image, tool, temporary))

    def test_no_os_release_is_an_error(self):
        class EmptyImage:
            def cat(self, path):
                return ""
        with self.assertRaisesRegex(ValueError, "inspection did not succeed"):
            module.inspect(EmptyImage(), Path("/not-used"))


if __name__ == "__main__":
    unittest.main()
