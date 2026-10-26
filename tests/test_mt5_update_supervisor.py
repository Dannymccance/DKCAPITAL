"""Offline tests of MT5 LiveUpdate handoff detection and account isolation."""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "ops/mt5/dkcapital-mt5-run"
loader = importlib.machinery.SourceFileLoader("mt5_supervisor", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class UpdateSupervisorTests(unittest.TestCase):
    def test_detects_new_utf16_log_handoff(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            log = directory / "20261017.log"
            log.write_bytes("old LiveUpdate start /update\n".encode("utf-16-le"))
            mark = module.log_offsets(directory)
            self.assertFalse(module.new_update_handoff(directory, mark))
            with log.open("ab") as out:
                out.write("LiveUpdate start terminal64.exe /update /portable\n".encode("utf-16-le"))
            self.assertTrue(module.new_update_handoff(directory, mark))

    def test_normal_shutdown_not_misclassified(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            log = directory / "20261017.log"
            log.write_bytes("Terminal started\n".encode("utf-16-le"))
            mark = module.log_offsets(directory)
            with log.open("ab") as out:
                out.write("Terminal exit with code 0\n".encode("utf-16-le"))
            self.assertFalse(module.new_update_handoff(directory, mark))

    def test_midnight_rollover_and_truncated_log(self):
        with tempfile.TemporaryDirectory() as d:
            directory = Path(d)
            earlier = directory / "20261017.log"
            earlier.write_bytes("old stuff\n".encode("utf-16-le"))
            mark = module.log_offsets(directory)
            later = directory / "20261018.log"
            later.write_bytes("LiveUpdate start terminal64.exe /update\n".encode("utf-16-le"))
            self.assertTrue(module.new_update_handoff(directory, mark))
            earlier.write_bytes("LiveUpdate start terminal64.exe /update\n".encode("utf-16-le"))
            self.assertTrue(module.new_update_handoff(directory, mark))

    def test_update_and_terminal_classification(self):
        target = Path("/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe")
        self.assertEqual(module.process_kind(str(target) + " /portable /profile:5057220041", target), "terminal")
        self.assertEqual(module.process_kind("C:\\users\\dkmt5\\liveupdate\\terminal64.exe /update /path:...", target), "updater")
        self.assertIsNone(module.process_kind("/var/lib/dkcapital-mt5-2/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe /portable", target))
        self.assertIsNone(module.process_kind("MetaEditor64.exe /compile:source.mq5", target))
        # Real 2026-10-18 LiveUpdate relaunch command observed on the VPS.
        self.assertEqual(
            module.process_kind(
                r"C:\Program Files\MetaTrader 5\terminal64.exe "
                "/skipupdate:8FE31F85A20F7884BA436F757AB5B932 /portable", target
            ),
            "terminal",
        )
        self.assertIsNone(module.process_kind(
            r"C:\Program Files\MetaTrader 4\terminal64.exe /portable", target
        ))
        self.assertIsNone(module.process_kind(
            r"C:\Program Files\MetaTrader 5\terminal64.exe /update /portable", target
        ))

    def test_update_wait_settles_without_updater(self):
        target = Path("/tmp/MT5/terminal64.exe")
        ticks = iter(range(0, 400, 11))
        with patch.object(module.time, "monotonic", side_effect=lambda: next(ticks)), \
             patch.object(module.time, "sleep", return_value=None), \
             patch.object(module, "wine_processes", return_value=set()):
            self.assertEqual(module.await_update(target, 1002), "relaunch")

    def test_update_wait_adopts_relaunched_terminal(self):
        target = Path("/tmp/MT5/terminal64.exe")
        with patch.object(module.time, "monotonic", return_value=0), \
             patch.object(module, "wine_processes", return_value={"terminal"}):
            self.assertEqual(module.await_update(target, 1002), "adopted")

    def test_updater_self_relaunch_is_adopted(self):
        target = Path("/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe")
        windows_command = r"C:\Program Files\MetaTrader 5\terminal64.exe /skipupdate:ABC /portable"
        with patch.object(module.time, "monotonic", return_value=0), \
             patch.object(module, "wine_processes", side_effect=lambda uid, exe: {module.process_kind(windows_command, exe)}):
            self.assertEqual(module.await_update(target, 1002), "adopted")

    def test_account1_uses_best_effort_skipupdate_only(self):
        exe = Path("/opt/mt5/terminal64.exe")
        account1 = module.launch_command("1", exe, "5057220041")
        account2 = module.launch_command("2", exe, None)
        self.assertIn("/skipupdate", account1)
        self.assertIn("/profile:5057220041", account1)
        self.assertNotIn("/skipupdate", account2)
        self.assertNotIn("/profile:5057220041", account2)

    def test_account1_approved_binary_enforces_pinned_hash(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "terminal64.exe"
            approval = Path(d) / "approved.sha256"
            exe.write_bytes(b"old good MT5")
            digest = hashlib.sha256(b"old good MT5").hexdigest()
            approval.write_text(digest + "\n")
            self.assertTrue(module.approved_binary(exe, approval)[0])
            exe.write_bytes(b"incompatible MT5 update")
            okay, message = module.approved_binary(exe, approval)
            self.assertFalse(okay)
            self.assertIn("MT5 executable changed", message)

    def test_missing_or_invalid_approval_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / "terminal64.exe"
            approval = Path(d) / "approved.sha256"
            exe.write_bytes(b"payload")
            self.assertFalse(module.approved_binary(exe, approval)[0])
            approval.write_text("invalid hash")
            self.assertFalse(module.approved_binary(exe, approval)[0])

    def test_accounts_remain_isolated(self):
        self.assertNotEqual(module.ACCOUNTS["1"][1], module.ACCOUNTS["2"][1])
        self.assertNotEqual(module.ACCOUNTS["1"][2], module.ACCOUNTS["2"][2])
        self.assertEqual(module.ACCOUNTS["1"][3], "5057220041")
        self.assertEqual(len(module.ACCOUNTS), 3)
        self.assertEqual(module.ACCOUNTS["3"][0], "dkmt5c")
        self.assertEqual(module.ACCOUNTS["3"][1], Path("/var/lib/dkcapital-mt5-3"))
        self.assertEqual(module.ACCOUNTS["3"][2], 152)
        self.assertIsNone(module.ACCOUNTS["3"][3])
        self.assertEqual(len({v[0] for v in module.ACCOUNTS.values()}), 3)
        self.assertEqual(len({v[1] for v in module.ACCOUNTS.values()}), 3)
        self.assertEqual(len({v[2] for v in module.ACCOUNTS.values()}), 3)
        self.assertIn("/skipupdate", module.launch_command("3", Path("/opt/mt5/terminal64.exe"), None))
        self.assertEqual(module.ACCOUNT3_APPROVAL_FILE, Path("/etc/dkcapital/mt5-account3-approved.sha256"))



if __name__ == "__main__":
    unittest.main()
