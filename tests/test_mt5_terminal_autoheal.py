"""Contract tests for opt-in DEMO-only frozen MT5 terminal recovery."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

scripts = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location(
    "dk_mt5_supervisor", scripts / "mt5_terminal_autoheal.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

LOGIN = 5057220041


def snapshot(now, *, positions=None):
    positions = [] if positions is None else positions
    return {
        "version": 2, "mode": "DEMO", "login": LOGIN, "currency": "GBP",
        "generated_at": (now - timedelta(seconds=320)).isoformat(),
        "received_at": (now - timedelta(seconds=320)).isoformat(),
        "entry_enabled": True, "positions": positions,
        "broker_positions_total": len(positions),
        "broker_orders_total": 0,
        "unmanaged_positions_count": 0,
        "broker_positions_protected": True,
    }


class Mt5RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 15, 20, 0, tzinfo=UTC)
        self.snapshot = snapshot(self.now)

    def gate(self, data=None, **kwargs):
        return module.recovery_gate(
            self.snapshot if data is None else data,
            expected_login=LOGIN, now=self.now, signals_ambiguous=False,
            gateway_ok=True, service_active=True, **kwargs,
        )

    def test_requires_180_seconds_persistent_staleness(self):
        eligible, reason = self.gate()
        self.assertTrue(eligible, reason)
        state, restart, _ = module.plan({}, eligible, 1000)
        self.assertFalse(restart)
        state, restart, _ = module.plan(state, eligible, 1179)
        self.assertFalse(restart)
        state, restart, _ = module.plan(state, eligible, 1180)
        self.assertTrue(restart)

    def test_rejects_old_untrusted_and_live_snapshots(self):
        for key, value in (
            ("mode", "LIVE"), ("login", LOGIN+1),
            ("broker_positions_total", None),
            ("broker_orders_total", 1),
            ("unmanaged_positions_count", 1),
            ("broker_positions_protected", False),
        ):
            changed = dict(self.snapshot)
            changed[key] = value
            self.assertFalse(self.gate(changed)[0], key)
        changed = dict(self.snapshot)
        changed["generated_at"] = (self.now-timedelta(minutes=12)).isoformat()
        self.assertFalse(self.gate(changed)[0])

    def test_mixed_and_unprotected_open_positions_fail(self):
        p = dict(self.snapshot)
        p["positions"] = [
            {"ticket": 1, "sl": 4100.0, "risk_gbp": 10.0},
        ]
        p["broker_positions_total"] = 1
        self.assertTrue(self.gate(p)[0])
        p["positions"] = [{"ticket": 1, "sl": 0.0}]
        self.assertFalse(self.gate(p)[0])
        p["positions"] = [{"ticket": 1, "sl": 4100.0}]
        p["broker_positions_protected"] = False
        self.assertFalse(self.gate(p)[0])

    def test_no_restart_if_unresolved_signals_or_upstream_outage(self):
        self.assertFalse(module.recovery_gate(
            self.snapshot, expected_login=LOGIN, now=self.now,
            signals_ambiguous=True, gateway_ok=True, service_active=True,
        )[0])
        self.assertFalse(module.recovery_gate(
            self.snapshot, expected_login=LOGIN, now=self.now,
            signals_ambiguous=False, gateway_ok=False, service_active=True,
        )[0])
        self.assertFalse(module.recovery_gate(
            self.snapshot, expected_login=LOGIN, now=self.now,
            signals_ambiguous=False, gateway_ok=True, service_active=False,
        )[0])

    def test_cooldown_and_hourly_restart_budget(self):
        state = {"first_seen": 1, "attempts": [1200]}
        _, allowed, why = module.plan(state, True, 1250)
        self.assertFalse(allowed)
        self.assertEqual(why, "restart cooldown")
        state = {"first_seen": 1, "attempts": [1200, 2400]}
        _, allowed, why = module.plan(state, True, 2500)
        self.assertFalse(allowed)
        self.assertEqual(why, "hourly restart budget exhausted")

    def test_legacy_telemetry_is_not_eligible(self):
        legacy = dict(self.snapshot)
        for k in (
            "broker_positions_total", "broker_orders_total",
            "unmanaged_positions_count", "broker_positions_protected",
        ):
            legacy.pop(k)
        self.assertFalse(self.gate(legacy)[0])

    def test_configured_login_from_env_file(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / ".env").write_text("MT5_ACCOUNT1_LOGIN=5057220041\nDISCORD_BOT_TOKEN=secret\n")
            with unittest.mock.patch.dict("os.environ", {"MT5_ACCOUNT1_LOGIN": ""}):
                self.assertEqual(module.configured_login(path), LOGIN)


if __name__ == "__main__":
    unittest.main()
