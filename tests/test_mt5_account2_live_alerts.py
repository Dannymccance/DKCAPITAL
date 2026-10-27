"""Read-only isolated Kyle REAL-GBP Discord alerts mirror Dan REAL-USD rules."""
from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.mt5_live_alerts import (
    account2_issues, account3_issues, live_account_issues,
)

NOW = datetime(2026, 10, 27, 0, 5, tzinfo=UTC)
KYLE = {
    "login": 24038172, "mode": "REAL", "currency": "GBP",
    "server": "VantageMarkets-Live 14",
}
DAN = {
    "login": 36193223, "mode": "REAL", "currency": "USD",
    "server": "VantageMarkets-Live 4",
}


def snapshot(account: int) -> dict:
    identity = KYLE if account == 2 else DAN
    bal = 883.71 if account == 2 else 1217.45
    return {
        "received_at": NOW.isoformat(),
        "generated_at": NOW.isoformat(),
        **identity,
        "feed_http": 200,
        "entry_enabled": True,
        "broker_positions_total": 0,
        "broker_orders_total": 0,
        "broker_positions_protected": True,
        "balance": bal,
        "equity": bal + (450 if account == 2 else 1583.50),
        "signal_risk_cap_pct": 4.0,
        "signal_risk_cap_account_currency": round(bal * .04, 2),
    }


class KyleDiscordLiveAlertTests(unittest.TestCase):
    def kyle(self, data, **kwargs):
        return account2_issues(data, NOW, **KYLE, **kwargs)

    def test_healthy_kyle_has_no_alerts(self):
        self.assertEqual(self.kyle(snapshot(2), expect_entries=True), set())

    def test_identity_stale_feed_broker_stops_risk_and_arm_failures(self):
        failures = [
            ({}, "MT5_ACCOUNT2_TELEMETRY_STALE"),
            ({"received_at": (NOW - timedelta(seconds=250)).isoformat()},
             "MT5_ACCOUNT2_TELEMETRY_STALE"),
            ({"login": 500}, "MT5_ACCOUNT2_WRONG_LOGIN"),
            ({"mode": "DEMO"}, "MT5_ACCOUNT2_WRONG_ACCOUNT_TYPE"),
            ({"server": "VantageMarkets-Live14"},
             "MT5_ACCOUNT2_WRONG_BROKER_SERVER"),
            ({"feed_http": 503}, "MT5_ACCOUNT2_INSTRUCTION_FEED_UNAVAILABLE"),
            ({"broker_positions_total": None}, "MT5_ACCOUNT2_EXPOSURE_UNVERIFIED"),
            ({"broker_positions_protected": False}, "MT5_ACCOUNT2_BROKER_STOPS_UNVERIFIED"),
            ({"signal_risk_cap_account_currency": 53.00},
             "MT5_ACCOUNT2_RISK_CAP_EXCEEDS_CASH_BALANCE"),
            ({"entry_enabled": False}, "MT5_ACCOUNT2_UNEXPECTEDLY_DISARMED"),
        ]
        for overrides, name in failures:
            with self.subTest(name=name):
                self.assertIn(
                    name,
                    self.kyle({**snapshot(2), **overrides}, expect_entries=True)
                )

    def test_host_recovery_scoped_to_kyle_and_no_false_dan_alert(self):
        host = {
            "checked_at": NOW.isoformat(),
            "accounts": {
                "2": {"active": False, "last_restart_at":
                      (NOW - timedelta(seconds=80)).isoformat()},
                "3": {"active": True, "last_restart_at": None},
            },
        }
        kyle = self.kyle(snapshot(2), host=host)
        self.assertIn("MT5_ACCOUNT2_TERMINAL_DOWN", kyle)
        self.assertIn("MT5_ACCOUNT2_TERMINAL_RECENTLY_RESTARTED", kyle)
        self.assertEqual(account3_issues(snapshot(3), NOW, **DAN, host=host), set())
        host["checked_at"] = (NOW - timedelta(minutes=5)).isoformat()
        self.assertEqual(self.kyle(snapshot(2), host=host), set())

    def test_same_alert_checks_and_separate_account_prefixes(self):
        a = {**snapshot(2), "feed_http": 503,
             "broker_positions_protected": False, "entry_enabled": False}
        b = {**snapshot(3), "feed_http": 503,
             "broker_positions_protected": False, "entry_enabled": False}
        k = {x.replace("ACCOUNT2", "ACCOUNTX") for x in self.kyle(a, expect_entries=True)}
        d = {x.replace("ACCOUNT3", "ACCOUNTX") for x in
             account3_issues(b, NOW, **DAN, expect_entries=True)}
        self.assertEqual(k, d)
        with self.assertRaises(ValueError):
            live_account_issues(a, NOW, account_id=1, **KYLE)

    def test_discord_wiring_includes_status_then_fault_recovery_without_trades(self):
        root = Path(__file__).resolve().parents[1]
        alerts = (root / "src/dkcapital/mt5_live_alerts.py").read_text()
        bot = (root / "src/dkcapital/discord_bot.py").read_text()
        example = (root / ".env.example").read_text()
        self.assertIn("1564421762752450592", bot)
        self.assertIn("MT5_ACCOUNT2_EXPECT_ENTRIES", bot)
        self.assertIn("MT5_ACCOUNT2_ALERT_CHANNEL_ID=1564421762752450592", example)
        self.assertIn("mt5-account2-alert-state.json", bot)
        self.assertIn("mt5-account2.json", bot)
        self.assertIn("mt5_account2_alert_loop.start()", bot)
        self.assertIn("update_mt5_account2_alerts()", bot)
        self.assertIn("rules = account2_issues", bot)
        self.assertIn("rules = account3_issues", bot)
        self.assertIn("initial monitoring status", bot.lower())
        for forbidden in ("systemctl", "PositionClose", "OrderSend", "WebRequest"):
            self.assertNotIn(forbidden, alerts)


if __name__ == "__main__":
    unittest.main()
