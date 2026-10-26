"""Guard scope and reversibility of Dan's MT5 compatibility rollback."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/mt5_account3_known_good.sh"
LAUNCHER = ROOT / "ops/mt5/dkcapital-mt5-run"


class DanKnownGoodTests(unittest.TestCase):
    def test_script_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backup_hashes_and_only_account3_write_path(self):
        s = SCRIPT.read_text()
        self.assertIn("2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1", s)
        self.assertIn("46b37c22cf9adf903d44ffb5590b892eeff1e5a6a5a852ebef95c6edd91f60be", s)
        self.assertIn("BACKUP=", s)
        self.assertIn('"$DST.next"', s)
        self.assertIn('mv -T "$DST.next" "$DST"', s)
        self.assertIn("Account3 Wine processes remain", s)
        for forbidden in ("systemctl start", "systemctl restart", "systemctl enable",
                          "docker compose", "apt install", "do-release-upgrade",
                          "killall", "cp -a", "rm -rf"):
            self.assertNotIn(forbidden, s)
        self.assertNotIn('mv -T "$SRC', s)
        self.assertNotIn('install -o dkmt5 -g dkmt5', s)

    def test_account3_launcher_verifies_before_after_and_refuses_liveupdate(self):
        s = LAUNCHER.read_text()
        self.assertIn('ACCOUNT3_APPROVAL_FILE = Path("/etc/dkcapital/mt5-account3-approved.sha256")', s)
        self.assertEqual(s.count('approved_binary(executable, approval_file)'), 3)
        self.assertIn('if account in ("1", "3"):', s)
        self.assertIn('if sys.argv[1] in ("1", "3"):', s)
        self.assertIn('RestartPreventExitStatus=78',
                      (ROOT / "ops/mt5/dkcapital-mt5-3.service").read_text())


if __name__ == "__main__":
    unittest.main()
