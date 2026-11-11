"""Tests for the atomic file write."""

import pathlib
import tempfile
import unittest
from unittest import mock

from trending_site import files


class AtomicWriteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)

    def test_writes_with_mode_and_creates_parents(self):
        for mode in (0o600, 0o644):
            with self.subTest(mode=oct(mode)):
                path = self.root / oct(mode) / "file.txt"
                files.atomic_write(path, "olá", mode)
                files.atomic_write(path, "tchau", mode)
                self.assertEqual(path.read_text(encoding="utf-8"), "tchau")
                self.assertEqual(path.stat().st_mode & 0o777, mode)
                self.assertEqual([p.name for p in path.parent.iterdir()], ["file.txt"])

    def test_failed_write_keeps_the_old_file_and_no_temp(self):
        path = self.root / "file.txt"
        files.atomic_write(path, "old", 0o644)
        with mock.patch("os.replace", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            files.atomic_write(path, "new", 0o644)
        self.assertEqual(path.read_text(), "old")
        self.assertEqual([p.name for p in self.root.iterdir()], ["file.txt"])

    def test_data_is_synced_before_the_rename(self):
        with mock.patch("os.fsync") as fsync:
            files.atomic_write(self.root / "file.txt", "x", 0o644)
        fsync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
