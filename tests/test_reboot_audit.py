"""Pure, offline tests of post-reboot MT5/Docker audit validation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "ops/recovery/check_boot.py"
spec = importlib.util.spec_from_file_location("dkcapital_boot_audit", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot(now):
    return {
        "login": 5057220041,
        "mode": "DEMO",
        "currency": "GBP",
        "signal_risk_cap_pct": 4.0,
        "daily_loss_guard_mode": "ALERT_ONLY",
        "feed_http": 200,
        "broker_positions_protected": True,
        "received_at": now.isoformat(),
    }


class BootAuditTests(unittest.TestCase):
    def test_healthy_snapshot_passes(self):
        now = datetime.now(UTC)
        self.assertEqual(module.telemetry_errors(snapshot(now), now), [])

    def test_old_telemetry_fails_even_if_broker_closed(self):
        now = datetime.now(UTC)
        data = snapshot(now)
        data["received_at"] = (now - timedelta(seconds=400)).isoformat()
        self.assertIn("MT5_ACCOUNT1_TELEMETRY_STALE", module.telemetry_errors(data, now))

    def test_missing_v205_and_feed_issues_fail(self):
        now = datetime.now(UTC)
        data = snapshot(now)
        data.update(signal_risk_cap_pct=None, daily_loss_guard_mode="LEGACY_OR_UNKNOWN", feed_http=0)
        problems = module.telemetry_errors(data, now)
        self.assertIn("MT5_ACCOUNT1_V205_NOT_VERIFIED", problems)
        self.assertIn("MT5_ACCOUNT1_FEED_UNAVAILABLE", problems)

    def test_wrong_login_and_native_stops_fail(self):
        now = datetime.now(UTC)
        data = snapshot(now)
        data.update(login=123, broker_positions_protected=False)
        problems = module.telemetry_errors(data, now)
        self.assertIn("MT5_ACCOUNT1_WRONG_LOGIN", problems)
        self.assertIn("MT5_ACCOUNT1_BROKER_STOP_PROTECTION_MISMATCH", problems)

    def test_service_missing_causes_audit_failure(self):
        now = datetime.now(UTC)
        data = snapshot(now)
        import json
        def fake_run(*args, timeout=12):
            if args[:2] == ("systemctl", "is-active"):
                return (False, "failed") if args[2] == "dkcapital-mt5-1.service" else (True, "active")
            if args[:2] == ("docker", "inspect"):
                return True, "true"
            if args[:2] == ("docker", "exec"):
                return True, json.dumps(data)
            raise AssertionError(args)

        with patch.object(module, "run", side_effect=fake_run):
            errors, details = module.collect()
        self.assertIn("UNIT_DOWN:dkcapital-mt5-1.service", errors)
        self.assertEqual(details["mt5_account1"]["login"], 5057220041)

    def test_no_broker_mutations(self):
        source = SCRIPT.read_text()
        self.assertNotIn("order_send", source)
        self.assertNotIn("systemctl restart", source)
        self.assertNotIn("docker start", source)


if __name__ == "__main__":
    unittest.main()
