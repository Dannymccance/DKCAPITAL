"""No MT5 or Discord access: verify isolated live-account alert predicates."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.mt5_live_alerts import account3_issues


NOW = datetime(2026, 10, 26, 22, 20, tzinfo=UTC)
IDENTITY = {
    "login": 36193223, "mode": "REAL", "currency": "USD",
    "server": "VantageMarkets-Live 4",
}


def healthy():
    return {
        "received_at": NOW.isoformat(),
        "generated_at": NOW.isoformat(),
        "login": 36193223, "mode": "REAL", "currency": "USD",
        "server": "VantageMarkets-Live 4", "feed_http": 200,
        "entry_enabled": True,
        "terminal_trade_allowed": True,
        "mql_trade_allowed": True,
        "broker_positions_total": 0, "broker_orders_total": 0,
        "broker_positions_protected": True,
        "balance": 1217.45, "equity": 2800.95,
        "signal_risk_cap_pct": 4.0,
        "signal_risk_cap_account_currency": 48.70,
        "positions": [],
    }


class Account3LiveAlertTests(unittest.TestCase):
    def issues(self, snapshot, **kwargs):
        return account3_issues(snapshot, NOW, **IDENTITY, **kwargs)

    def test_healthy_real_profile_has_no_alerts(self):
        self.assertEqual(self.issues(healthy(), expect_entries=True), set())

    def test_missing_or_stale_telemetry_alerts(self):
        self.assertIn("MT5_ACCOUNT3_TELEMETRY_STALE", self.issues({}))
        stale = {**healthy(), "received_at": (NOW - timedelta(seconds=200)).isoformat()}
        self.assertIn("MT5_ACCOUNT3_TELEMETRY_STALE", self.issues(stale))
        self.assertNotIn("MT5_ACCOUNT3_INSTRUCTION_FEED_UNAVAILABLE", self.issues(stale))

    def test_bad_feed_recovery_is_account3_specific(self):
        bad = {**healthy(), "feed_http": 503}
        self.assertIn("MT5_ACCOUNT3_INSTRUCTION_FEED_UNAVAILABLE", self.issues(bad))
        self.assertNotIn("MT5_ACCOUNT3_INSTRUCTION_FEED_UNAVAILABLE", self.issues(healthy()))
        self.assertFalse(any(code.startswith("MT5_ACCOUNT1") for code in self.issues(bad)))

    def test_wrong_identity_and_unverified_protection(self):
        bad = {**healthy(), "login": 777, "mode": "DEMO",
               "server": "Other-Live", "broker_positions_total": None,
               "broker_positions_protected": False}
        codes = self.issues(bad)
        for code in (
            "MT5_ACCOUNT3_WRONG_LOGIN",
            "MT5_ACCOUNT3_WRONG_ACCOUNT_TYPE",
            "MT5_ACCOUNT3_WRONG_BROKER_SERVER",
            "MT5_ACCOUNT3_EXPOSURE_UNVERIFIED",
            "MT5_ACCOUNT3_BROKER_STOPS_UNVERIFIED",
        ):
            self.assertIn(code, codes)

    def test_entry_switch_is_alert_only_and_explicitly_configured(self):
        no = {**healthy(), "entry_enabled": False}
        self.assertEqual(self.issues(no), set())
        self.assertIn("MT5_ACCOUNT3_UNEXPECTEDLY_DISARMED",
                      self.issues(no, expect_entries=True))
        self.assertIn("MT5_ACCOUNT3_UNEXPECTEDLY_ARMED",
                      self.issues(healthy(), expect_entries=False))

    def test_risk_ceiling_flags_credit_inflation(self):
        bad = {**healthy(), "signal_risk_cap_account_currency": 112.04}
        self.assertIn("MT5_ACCOUNT3_RISK_CAP_EXCEEDS_CASH_BALANCE", self.issues(bad))

    def test_host_state_if_audited_and_fresh(self):
        h = {"checked_at": NOW.isoformat(), "accounts": {
            "3": {"active": False, "last_restart_at": (NOW - timedelta(seconds=100)).isoformat()},
        }}
        problems = self.issues(healthy(), host=h)
        self.assertIn("MT5_ACCOUNT3_TERMINAL_DOWN", problems)
        self.assertIn("MT5_ACCOUNT3_TERMINAL_RECENTLY_RESTARTED", problems)
        h["checked_at"] = (NOW - timedelta(seconds=600)).isoformat()
        self.assertEqual(self.issues(healthy(), host=h), set())

    def test_isolation_and_no_broker_commands(self):
        root = Path(__file__).resolve().parents[1]
        alerts = (root / "src/dkcapital/mt5_live_alerts.py").read_text()
        bot = (root / "src/dkcapital/discord_bot.py").read_text()
        self.assertIn("1564401829796778124", bot)
        self.assertIn("mt5-account3-alert-state.json", bot)
        self.assertIn("mt5_account3_alert_loop.start()", bot)
        self.assertIn("update_mt5_account3_alerts()", bot)
        self.assertNotIn("systemctl", alerts)
        self.assertNotIn("PositionClose", alerts)


if __name__ == "__main__":
    unittest.main()
