"""Profile-scoped safety tests; no real MT5 or account modifications."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dkcapital.mt5_profile_registry import profiles_from_registry
from dkcapital.watchdog import check_health


class AccountMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.now = datetime.now(UTC)
        self.write("telegram-recovery-status.json",
                   {"checked_at": self.now.isoformat(), "healthy": True})
        self.write("discord-heartbeat.json", {"checked_at": self.now.isoformat()})
        self.write("signal-processor-heartbeat.json", {"checked_at": self.now.isoformat()})
        self.write("mt5-account1.json", {
            "received_at": self.now.isoformat(), "feed_http": 200,
            "entry_enabled": False, "login": 5057220041, "mode": "DEMO",
            "currency": "GBP", "broker_positions_total": 0,
            "broker_orders_total": 0, "broker_positions_protected": True,
        })

    def write(self, name, payload):
        (self.data / name).write_text(json.dumps(payload))

    def registry(self, *, id2_enabled=False, id2_mode="DEMO"):
        self.write("mt5-monitor-profiles.json", {"accounts": [
            {"id": 1, "enabled": True, "label": "Paper",
             "expected_mode": "DEMO", "expected_currency": "GBP",
             "expected_login": 5057220041, "expect_entries": False},
            {"id": 2, "enabled": id2_enabled, "label": "Kyle",
             **({"expected_mode": id2_mode, "expected_currency": "USD",
                 "expected_login": 123456789, "expect_entries": False}
                if id2_enabled else {})},
            {"id": 3, "enabled": False},
        ]})

    def host(self, age_seconds=0, active2=True, restarted2=None):
        self.write("mt5-profile-host-status.json", {
            "checked_at": (self.now-timedelta(seconds=age_seconds)).isoformat(),
            "accounts": {
                "1": {"active": True},
                "2": {"active": active2, "last_restart_at": restarted2},
            },
        })

    def test_uncommissioned_accounts_do_not_trigger_alarms(self):
        self.registry()
        self.host()
        self.assertEqual(check_health(self.data, self.now, True), set())

    def test_enabled_profile_requires_its_own_fresh_broker_telemetry(self):
        self.registry(id2_enabled=True)
        self.host()
        errors = check_health(self.data, self.now, True)
        self.assertIn("MT5_ACCOUNT2_TELEMETRY_STALE", errors)
        self.assertNotIn("MT5_ACCOUNT1_TELEMETRY_STALE", errors)

    def test_wrong_identity_unprotected_orders_and_missing_feed_alert(self):
        self.registry(id2_enabled=True)
        self.host()
        self.write("mt5-account2.json", {
            "received_at": self.now.isoformat(),
            "mode": "DEMO", "currency": "GBP", "login": 7777,
            "feed_http": 1003, "entry_enabled": True,
            "broker_positions_total": 1, "broker_orders_total": None,
            "broker_positions_protected": False,
        })
        issues = check_health(self.data, self.now, True)
        for code in (
            "MT5_ACCOUNT2_WRONG_LOGIN",
            "MT5_ACCOUNT2_WRONG_ACCOUNT_TYPE",
            "MT5_ACCOUNT2_FEED_NOT_HTTP200",
            "MT5_ACCOUNT2_UNEXPECTEDLY_ARMED",
            "MT5_ACCOUNT2_EXPOSURE_UNVERIFIED",
            "MT5_ACCOUNT2_BROKER_STOPS_UNVERIFIED",
        ):
            self.assertIn(code, issues)

    def test_down_restart_and_stale_host_status_are_account_specific(self):
        self.registry(id2_enabled=True)
        self.host(active2=False, restarted2=self.now.isoformat())
        problems = check_health(self.data, self.now, True)
        self.assertIn("MT5_ACCOUNT2_TERMINAL_DOWN", problems)
        self.assertIn("MT5_ACCOUNT2_TERMINAL_RECENTLY_RESTARTED", problems)
        self.assertNotIn("MT5_ACCOUNT1_TERMINAL_DOWN", problems)
        self.host(age_seconds=300)
        self.assertIn("MT5_PROFILE_HOST_AUDIT_STALE",
                      check_health(self.data, self.now, True))

    def test_invalid_config_always_alerts_and_preserves_account1(self):
        self.write("mt5-monitor-profiles.json", {
            "accounts": [{"id": 1, "enabled": False}]
        })
        profiles, valid = profiles_from_registry(self.data)
        self.assertFalse(valid)
        self.assertEqual([p["id"] for p in profiles], [1])
        self.assertIn("MT5_PROFILE_REGISTRY_INVALID",
                      check_health(self.data, self.now, True))

    def test_duplicate_profile_fails_closed(self):
        self.registry()
        raw = json.loads((self.data/"mt5-monitor-profiles.json").read_text())
        raw["accounts"].append(raw["accounts"][0])
        self.write("mt5-monitor-profiles.json", raw)
        self.assertFalse(profiles_from_registry(self.data)[1])

    def test_legacy_account1_watchdog_unchanged_without_registry(self):
        # Existing tests require an armed default demo profile when no
        # commissioned registry has yet been installed.
        self.assertIn("MT5_ACCOUNT1_NOT_ARMED",
                      check_health(self.data, self.now, True))


if __name__ == "__main__":
    unittest.main()
