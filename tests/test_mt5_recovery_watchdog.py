"""Watchdog delivery and commissioning safety for fleet MT5 recovery."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from dkcapital.mt5_profile_registry import profiles_from_registry
from dkcapital.watchdog import Watchdog, check_health


class RecoveryAlertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime.now(UTC)
        self.write("mt5-account1.json", {
            "received_at": self.now.isoformat(), "feed_http": 200,
            "entry_enabled": True, "login": 5057220041,
            "mode": "DEMO", "currency": "GBP",
            "broker_positions_total": 0, "broker_orders_total": 0,
            "broker_positions_protected": True,
        })
        for file, info in (
            ("telegram-recovery-status.json", {"checked_at": self.now.isoformat(), "healthy": True}),
            ("discord-heartbeat.json", {"checked_at": self.now.isoformat()}),
            ("signal-processor-heartbeat.json", {"checked_at": self.now.isoformat()}),
            ("mt5-profile-host-status.json", {"checked_at": self.now.isoformat(),
                                               "accounts": {"1": {"active": True}}}),
        ):
            self.write(file, info)
        self.write("mt5-monitor-profiles.json", {"accounts": [{
            "id": 1, "enabled": True, "label": "MT5 demo",
            "expected_login": 5057220041,
            "expected_mode": "DEMO", "expected_currency": "GBP",
            "expect_entries": True,
            "auto_recover": True, "recovery_policy": "broker_protected",
        }]})
        self.event = {"account": 1, "result": "RESTART_REQUESTED",
                      "at": self.now.isoformat(), "id": f"1:{self.now.isoformat()}:RESTART_REQUESTED"}
        self.write("mt5-recovery-status.json", {
            "updated_at": self.now.isoformat(),
            "accounts": {"1": {"result": "RESTART_REQUESTED"}},
            "events": [self.event],
        })

    def write(self, name, data):
        (self.root/name).write_text(json.dumps(data))

    def test_explicit_approval_required_to_turn_on_recovery(self):
        config = json.loads((self.root/"mt5-monitor-profiles.json").read_text())
        config["accounts"][0]["recovery_policy"] = "disabled"
        self.write("mt5-monitor-profiles.json", config)
        _, ok = profiles_from_registry(self.root)
        self.assertFalse(ok)

    def test_missing_recovery_supervisor_status_alerts_only_if_commissioned(self):
        (self.root/"mt5-recovery-status.json").unlink()
        self.assertIn("MT5_RECOVERY_SUPERVISOR_STALE",
                      check_health(self.root, self.now, True))

    def test_recovery_failure_generates_broker_account_specific_alert(self):
        data = {
            "updated_at": self.now.isoformat(),
            "accounts": {"1": {"result": "WAITING_FOR_RECONNECT"}},
            "events": [],
        }
        self.write("mt5-recovery-status.json", data)
        self.assertIn("MT5_ACCOUNT1_WAITING_FOR_RECONNECT",
                      check_health(self.root, self.now, True))

    def test_durable_events_delivered_once_after_watchdog_restart(self):
        env = {
            "WATCHDOG_DATA_PATH": str(self.root),
            "MT5_ALERT_CHANNEL_ID": "123456789012345678",
            "DISCORD_BOT_TOKEN": "placeholder-token-not-real",
        }
        sent = []
        with patch.dict("os.environ", env):
            watcher = Watchdog()
            watcher._gateway_ok = lambda: True
            watcher._notify = lambda content, **kw: sent.append(content) or True
            watcher.cycle()
            self.assertEqual(sum("RESTART_REQUESTED" in m for m in sent), 1)
            watcher.cycle()
            self.assertEqual(sum("RESTART_REQUESTED" in m for m in sent), 1)

            # A subsequent transition remains independently deliverable.
            recovered = {"account": 1, "result": "RECOVERED",
                         "at": self.now.isoformat(), "id": f"1:{self.now.isoformat()}:RECOVERED"}
            state = json.loads((self.root/"mt5-recovery-status.json").read_text())
            state["events"].append(recovered)
            self.write("mt5-recovery-status.json", state)
            replacement = Watchdog()
            replacement._gateway_ok = lambda: True
            replacement._notify = lambda content, **kw: sent.append(content) or True
            replacement.cycle()
            self.assertEqual(sum("recovery: RECOVERED" in m for m in sent), 1)


if __name__ == "__main__":
    unittest.main()
