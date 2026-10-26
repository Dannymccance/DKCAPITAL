"""Account3 host commissioning leaves existing MT5 accounts untouched."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Account3HostCommissioning(unittest.TestCase):
    def test_shell_syntax(self):
        for path in ("scripts/prepare_mt5_account3.sh",
                     "ops/mt5/mt5-account3-installer-display"):
            with self.subTest(path=path):
                p = subprocess.run(["bash", "-n", str(ROOT / path)],
                                   capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_no_existing_terminal_changes_or_automatic_start(self):
        sh = (ROOT / "scripts/prepare_mt5_account3.sh").read_text()
        for forbidden in ("/var/lib/dkcapital-mt5/.wine",
                          "/var/lib/dkcapital-mt5-2/.wine",
                          "systemctl start", "systemctl restart", "systemctl enable",
                          "docker compose", "apt upgrade", "do-release-upgrade",
                          "cp -a", "rm -rf", "curl ", "wget "):
            self.assertNotIn(forbidden, sh)
        self.assertIn("check_target ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run-account3", sh)
        self.assertIn("install -o root -g root -m 0755 ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run-account3", sh)
        self.assertNotIn("install -o root -g root -m 0755 ops/mt5/dkcapital-mt5-run /usr/local/bin/dkcapital-mt5-run\n", sh)
        self.assertIn("ExecStart=/usr/local/bin/dkcapital-mt5-run-account3 3",
                      (ROOT / "ops/mt5/dkcapital-mt5-3.service").read_text())
        for required in ("[[ ! -S /tmp/.X11-unix/X152 ]]",
                         "useradd --system --user-group --create-home",
                         "systemctl daemon-reload", "cmp -s",
                         "dkcapital-mt5-3.service", "dkcapital-vnc3.service"):
            self.assertIn(required, sh)

    def test_temporary_display_has_no_autostart_or_broker(self):
        sh = (ROOT / "ops/mt5/mt5-account3-installer-display").read_text()
        unit = (ROOT / "ops/mt5/dkcapital-mt5-3-installer-display.service").read_text()
        self.assertIn("/usr/bin/Xvfb :152", sh)
        self.assertIn("-nolisten tcp", sh)
        self.assertIn("xauth -f", sh)
        self.assertIn("User=dkmt5c", unit)
        self.assertIn("Restart=no", unit)
        self.assertNotIn("WantedBy=", unit)
        self.assertNotIn("ExecStart=/usr/local/bin/dkcapital-mt5-run 3", unit)
        vnc = (ROOT / "ops/recovery/dkcapital-vnc3.service").read_text()
        self.assertIn("-rfbport 5904 -localhost", vnc)


if __name__ == "__main__":
    unittest.main()
