"""Offline regression tests: recovery fails closed."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

file = Path(__file__).resolve().parents[1] / "scripts/mt5_liveupdate_recovery.py"
spec = importlib.util.spec_from_file_location("liveupdate_recovery", file)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class SafetyTests(unittest.TestCase):
    def test_demo_requires_no_exposure(self):
        base = dict(mode="DEMO", login=123, broker_positions_total=0,
                    broker_orders_total=0, unmanaged_positions_count=0,
                    positions=[], orders=[], generated_at="2026-10-22T12:00:00Z")
        self.assertTrue(mod.safe_demo_snapshot(base, 123, 1792670400))
        for field in ("broker_positions_total", "broker_orders_total", "unmanaged_positions_count"):
            other = base | {field: 1}
            self.assertFalse(mod.safe_demo_snapshot(other, 123, 1792670400))
        self.assertFalse(mod.safe_demo_snapshot(base | {"mode":"LIVE"}, 123, 1792670400))
        self.assertFalse(mod.safe_demo_snapshot(base, 321, 1792670400))
        self.assertFalse(mod.safe_demo_snapshot(base, 123, 1792675000))

    def test_accounts_disabled_by_default(self):
        self.assertEqual(mod.recover({"id": 2, "enabled": False}), "DISABLED")

    def test_rejects_unexpected_service(self):
        self.assertIn("BLOCKED", mod.recover({"id":1,"enabled":True,"service":"ssh.service"}))

    def test_exit_code_required(self):
        class Result:
            returncode = 0
            stdout = "ActiveState=failed\\nExecMainStatus=1\\n"
        with patch.object(mod, "command", return_value=Result()):
            self.assertFalse(mod.failure_is_liveupdate("dkcapital-mt5-1.service"))


if __name__ == "__main__":
    unittest.main()
