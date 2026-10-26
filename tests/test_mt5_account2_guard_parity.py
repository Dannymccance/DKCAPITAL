"""Fail-closed Account2 binary guard installer is offline-testable."""
from __future__ import annotations
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kyle_mt5_guard", ROOT / "scripts/commission_mt5_account2_guard.py")
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


class KyleGuardTests(unittest.TestCase):
    def test_approved_launcher_fails_closed_on_kyle_unapproved_binary(self):
        import importlib.machinery
        import hashlib
        launcher = ROOT / "ops/mt5/dkcapital-mt5-run"
        loader = importlib.machinery.SourceFileLoader("mt5_guarded_launcher", str(launcher))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        self.assertEqual(module.ACCOUNT2_APPROVAL_FILE,
                         Path("/etc/dkcapital/mt5-account2-approved.sha256"))
        self.assertEqual(module.APPROVAL_FAILURE_EXIT, 78)
        self.assertIn("/skipupdate",
                      module.launch_command("2", Path("/tmp/terminal64.exe"), None))
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "terminal64.exe"
            pin = Path(temp) / "account2.sha256"
            exe.write_bytes(b"known good compiler test")
            pin.write_text(hashlib.sha256(exe.read_bytes()).hexdigest() + "\n")
            self.assertTrue(module.approved_binary(exe, pin)[0])
            exe.write_bytes(b"changed by auto-update")
            self.assertFalse(module.approved_binary(exe, pin)[0])

    def test_installer_defaults_to_audit_and_never_restarts_mt5(self):
        installer = (ROOT / "scripts/commission_mt5_account2_guard.py").read_text()
        self.assertIn('parser.add_argument("--install"', installer)
        self.assertIn('shutil.copy2(LAUNCHER, backup / "common-launcher.before")', installer)
        self.assertIn('write_root_file(APPROVAL2,', installer)
        self.assertIn('write_root_file(DROPIN,', installer)
        self.assertIn('write_root_file(LAUNCHER,', installer)
        self.assertIn('run("systemctl", "daemon-reload")', installer)
        self.assertNotIn('run("systemctl", "restart"', installer)
        self.assertNotIn('run("systemctl", "stop"', installer)
        self.assertNotIn('run("systemctl", "start"', installer)
        self.assertNotIn('subprocess.Popen(', installer)

    def test_root_file_atomic_writer_has_appropriate_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "approval"
            GUARD.write_root_file(path, b"known-good-hash\n", 0o644)
            self.assertEqual(path.read_text(), "known-good-hash\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            GUARD.write_root_file(path, b"new\n", 0o600)
            self.assertEqual(path.read_text(), "new\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
