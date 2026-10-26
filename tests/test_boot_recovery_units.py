"""Offline static contracts for DK Capital boot units and installer safety."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops/recovery"


class BootRecoveryUnitTests(unittest.TestCase):
    def test_core_stack_starts_all_expected_services(self):
        text = (OPS / "dkcapital-stack.service").read_text()
        for name in (
            "telegram-listener", "signal-processor", "paper-engine",
            "mt5-gateway", "dkcapital-watchdog", "discord-bot"
        ):
            self.assertIn(name, text)
        self.assertIn("--no-build", text)
        self.assertNotIn("bybit-demo", text)

    def test_mt5_restart_does_not_exhaust_start_limit(self):
        text = (OPS / "mt5-restart.conf").read_text()
        self.assertIn("StartLimitIntervalSec=0", text)
        self.assertIn("Restart=always", text)
        self.assertIn("RestartSec=120", text)

    def test_vnc_is_local_and_password_protected(self):
        for number, display, port in (("1", ":150", "5901"), ("2", ":151", "5902")):
            with self.subTest(account=number):
                text = (OPS / f"dkcapital-vnc{number}.service").read_text()
                self.assertIn("-localhost", text)
                self.assertIn("-rfbauth", text)
                self.assertIn(f"-display {display}", text)
                self.assertIn(f"-rfbport {port}", text)
                self.assertIn("Restart=always", text)
                self.assertNotIn("ExecStart=/usr/bin/x11vnc -bg", text)

    def test_account3_is_a_staged_unit_and_never_started_by_recovery(self):
        unit = (ROOT / "ops/mt5/dkcapital-mt5-3.service").read_text()
        self.assertIn("User=dkmt5c", unit)
        self.assertIn("WorkingDirectory=/var/lib/dkcapital-mt5-3", unit)
        self.assertIn("ExecStart=/usr/local/bin/dkcapital-mt5-run-account3 3", unit)
        installer = (OPS / "install-recovery.sh").read_text()
        self.assertNotIn("dkcapital-mt5-3.service", installer)
        self.assertNotIn("dkcapital-vnc3.service", installer)
        audit = (OPS / "check_boot.py").read_text()
        self.assertNotIn('dkcapital-mt5-3.service', audit)
        self.assertNotIn('dkcapital-vnc3.service', audit)

    def test_audit_is_scheduled_after_boot(self):
        text = (OPS / "dkcapital-boot-audit.timer").read_text()
        self.assertIn("OnBootSec=4min", text)
        self.assertIn("OnUnitActiveSec=5min", text)

    def test_installer_enables_but_never_restarts_running_services(self):
        text = (OPS / "install-recovery.sh").read_text()
        self.assertIn("systemctl enable", text)
        self.assertNotIn("systemctl restart", text)
        self.assertNotIn("systemctl start", text)
        self.assertNotIn("docker compose up", text)
        self.assertNotIn("systemctl enable --now", text)


if __name__ == "__main__":
    unittest.main()
