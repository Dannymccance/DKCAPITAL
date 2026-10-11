from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.watchdog import check_health


class WatchdogHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(UTC)
        self._write("mt5-account1.json", {
            "received_at": self.now.isoformat(), "feed_http": 200,
            "entry_enabled": True,
        })
        self._write("telegram-recovery-status.json", {
            "checked_at": self.now.isoformat(), "healthy": True,
        })
        self._write("signal-processor-heartbeat.json", {
            "checked_at": self.now.isoformat(),
        })
        self._write("discord-heartbeat.json", {
            "checked_at": self.now.isoformat(),
        })

    def _write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def test_healthy_services(self):
        self.assertEqual(check_health(self.root, self.now, True), set())

    def test_warns_on_incomplete_catchup(self):
        self._write("telegram-recovery-status.json", {
            "checked_at": self.now.isoformat(), "healthy": False,
        })
        self.assertIn("TELEGRAM_CATCHUP_INCOMPLETE",
                      check_health(self.root, self.now, True))

    def test_stale_discord_even_when_gateway_alive(self):
        old = (self.now - timedelta(minutes=5)).isoformat()
        self._write("discord-heartbeat.json", {"checked_at": old})
        problems = check_health(self.root, self.now, True)
        self.assertIn("DISCORD_BOT_HEARTBEAT_STALE", problems)
        self.assertNotIn("GATEWAY_HEALTH_UNAVAILABLE", problems)

    def test_disarmed_and_invalid_feed_are_visible(self):
        self._write("mt5-account1.json", {
            "received_at": self.now.isoformat(), "feed_http": 1003,
            "entry_enabled": False,
        })
        problems = check_health(self.root, self.now, True)
        self.assertIn("MT5_ACCOUNT1_NOT_ARMED", problems)
        self.assertIn("MT5_ACCOUNT1_FEED_NOT_HTTP200", problems)

    def test_missing_listener_and_processor_heartbeats(self):
        (self.root / "telegram-recovery-status.json").unlink()
        (self.root / "signal-processor-heartbeat.json").unlink()
        problems = check_health(self.root, self.now, False)
        self.assertIn("TELEGRAM_CATCHUP_HEARTBEAT_STALE", problems)
        self.assertIn("SIGNAL_PROCESSOR_HEARTBEAT_STALE", problems)
        self.assertIn("GATEWAY_HEALTH_UNAVAILABLE", problems)


if __name__ == "__main__":
    unittest.main()
