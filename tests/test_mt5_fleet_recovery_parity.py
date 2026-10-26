"""Fleet restart/boot policy parity checks are read-only and broker scoped."""
from __future__ import annotations
import json
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts.mt5_fleet_recovery_parity import parity_report, snapshot_health

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 26, 22, 30, tzinfo=UTC)


class RecoveryParityTests(unittest.TestCase):
    def test_parity_uses_each_account_snapshot_and_detects_missing_live_reconciliation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for ident in (1, 2, 3):
                (root / f"mt5-account{ident}.json").write_text(json.dumps({
                    "received_at": NOW.isoformat(),
                    "login": ident * 1000, "feed_http": 200,
                    "mode": "DEMO" if ident == 1 else "REAL",
                    "currency": "GBP" if ident == 1 else "USD",
                    "broker_positions_protected": True,
                    "restart_reconciliation_supported": False
                }))
            with patch("scripts.mt5_fleet_recovery_parity._show") as show:
                show.side_effect = lambda unit: {
                    "enabled": unit.endswith("1.service"),
                    "active": True, "restart": "always",
                }
                result = parity_report(root, NOW)
        self.assertEqual(result["accounts"]["1"]["broker"]["mode"], "DEMO")
        self.assertTrue(result["accounts"]["1"]["crash_recovery_configured"])
        self.assertTrue(result["accounts"]["3"]["crash_recovery_configured"])
        self.assertFalse(result["accounts"]["3"]["boot_recovery_configured"])
        self.assertFalse(result["accounts"]["3"]["frozen_terminal_recovery_eligible"])
        self.assertTrue(result["accounts"]["3"]["telemetry"]["feed_ok"])

    def test_stale_snapshot_never_certified(self):
        old = (NOW - timedelta(minutes=10)).isoformat()
        result = snapshot_health({"received_at": old, "feed_http": 200}, NOW)
        self.assertFalse(result["authenticated_recent"])

    def test_issuer_never_restarts_trading_and_requires_explicit_enable(self):
        file = ROOT / "scripts/enable_account3_boot_parity.py"
        contents = file.read_text()
        self.assertIn('parser.add_argument("--enable-boot"', contents)
        self.assertIn('call("systemctl", "enable", *UNITS)', contents)
        self.assertNotIn('call("systemctl", "restart"', contents)
        self.assertNotIn('call("systemctl", "start"', contents)
        self.assertIn('broker.get("broker_positions_protected") is True', contents)
        self.assertIn('broker.get("unmanaged_orders_count") == 0', contents)

    def test_python_syntax(self):
        for p in ("scripts/enable_account3_boot_parity.py",
                  "scripts/mt5_fleet_recovery_parity.py"):
            result = subprocess.run(
                ["python3", "-m", "py_compile", str(ROOT / p)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
