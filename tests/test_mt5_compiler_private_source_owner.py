"""Regression: root-created token-bearing EA must be accessible only to MetaEditor user."""
from __future__ import annotations
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.generate_mt5_v211_profile import write_compiler_source


class CompilerSourceOwnerTests(unittest.TestCase):
    def test_private_source_created_mode_0600_without_reveal_or_overwrite(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            file = folder / "Kyle_Private.mq5"
            secret = "private-unique-token-test-only"
            write_compiler_source(file, secret, folder)
            self.assertEqual(file.read_text(), secret)
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            self.assertEqual(file.stat().st_uid, folder.stat().st_uid)
            with self.assertRaises(FileExistsError):
                write_compiler_source(file, "overwritten", folder)
            self.assertEqual(file.read_text(), secret)

    def test_root_compiler_workspace_uses_fd_based_owner_transfer(self):
        with tempfile.TemporaryDirectory() as work:
            folder = Path(work)
            file = folder / "KylePrivate.mq5"
            with patch("scripts.generate_mt5_v211_profile.os.geteuid", return_value=0), \
                 patch("scripts.generate_mt5_v211_profile.os.fchown") as owner:
                write_compiler_source(file, "confidential", folder)
                owner.assert_called_once()
                args = owner.call_args.args
                self.assertEqual(args[1:], (folder.stat().st_uid, folder.stat().st_gid))
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)

    def test_symlink_output_directory_rejected(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            directory = root / "compiler"
            directory.mkdir()
            link = root / "symlink"
            link.symlink_to(directory, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "isolated compiler"):
                write_compiler_source(link / "unsafe.mq5", "private", link)


if __name__ == "__main__":
    unittest.main()
