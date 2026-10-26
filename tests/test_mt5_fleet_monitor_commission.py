"""Offline guarantees that fleet commissioning does not arm or restart REAL MT5."""
from __future__ import annotations
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "scripts/commission_mt5_fleet_monitoring.py"
spec = importlib.util.spec_from_file_location("fleet_monitor", FILE)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class FleetMonitorCommissionTests(unittest.TestCase):
    def test_initial_registry_registers_all_three_but_keeps_real_autoheal_off(self):
        result = monitor.propose(None)
        accounts = result["accounts"]
        self.assertEqual([r["id"] for r in accounts], [1, 2, 3])
        self.assertEqual([r["expected_login"] for r in accounts],
                         [5057220041, 24038172, 36193223])
        self.assertEqual([r["expected_currency"] for r in accounts],
                         ["GBP", "GBP", "USD"])
        self.assertTrue(all(r["expect_entries"] for r in accounts))
        self.assertTrue(all(not r["auto_recover"] for r in accounts))
        self.assertTrue(all(r["recovery_policy"] == "disabled" for r in accounts))
        self.assertEqual(len(monitor.verify_registry_contents(result)), 3)

    def test_existing_account1_settings_are_preserved(self):
        existing = {"accounts": [{
            **monitor.PROFILES[0], "label": "Original Account1",
            "auto_recover": True, "recovery_policy": "broker_protected"
        }], "original_metadata": "preserve"}
        result = monitor.propose(existing)
        self.assertEqual(result["original_metadata"], "preserve")
        self.assertTrue(result["accounts"][0]["auto_recover"])
        self.assertEqual(result["accounts"][0]["label"], "Original Account1")
        self.assertEqual([r["id"] for r in result["accounts"]], [1, 2, 3])
        monitor.verify_registry_contents(result)

    def test_existing_real_recovery_or_wrong_identity_fails_closed(self):
        for original in (
            {**monitor.PROFILES[1], "expected_login": 1234},
            {**monitor.PROFILES[1], "auto_recover": True,
             "recovery_policy": "broker_protected"},
            {**monitor.PROFILES[1], "enabled": False},
        ):
            with self.subTest(original=original), self.assertRaises(ValueError):
                monitor.propose({"accounts": [dict(monitor.PROFILES[0]), original]})

    def test_no_restart_or_broker_action_path(self):
        code = FILE.read_text()
        self.assertIn('parser.add_argument("--install"', code)
        self.assertIn("assess(private_values(), expect_all_armed=True)", code)
        self.assertIn('systemctl", "is-active"', code)
        self.assertNotIn('systemctl", "restart"', code)
        self.assertNotIn('systemctl", "enable"', code)
        self.assertNotIn('systemctl", "start"', code)
        self.assertNotIn("OrderSend", code)
        self.assertNotIn("CTrade", code)

    def test_dry_run_does_not_write_registry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = {"overall": "PASS"}
            class Proc:
                returncode = 0
                stdout = "active\n"
            with patch.object(monitor, "discover_data_dir", return_value=root), \
                 patch.object(monitor, "assess", return_value=result), \
                 patch.object(monitor, "private_values", return_value={}), \
                 patch.object(monitor.subprocess, "run", return_value=Proc()):
                out = monitor.commission(install=False, compose_dir=root)
            self.assertEqual(out["mode"], "AUDIT_ONLY")
            self.assertFalse((root / "mt5-monitor-profiles.json").exists())


if __name__ == "__main__":
    unittest.main()
