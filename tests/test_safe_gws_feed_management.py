"""GWS V5 entry hold never removes management rows or provider actions."""
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler


class FeedRecorder(Mt5GatewayHandler):
    def __init__(self, config):
        self._config = config
        self.body = ""
    @property
    def config(self):
        return self._config
    def _write_text(self, code, body):
        self.body = body


class SafeFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "signals.json"
        with patch.dict(os.environ, {
            "SIGNAL_STATE_PATH": str(self.path),
            "MT5_GATEWAY_TOKEN": "a" * 32,
            "MT5_DEMO_FEED_ENABLED": "true",
        }):
            self.config = GatewayConfig()
        self.now = datetime.now(UTC)
        self.signal = {
            "signal_id": "-100:123", "symbol": "XAUUSD", "status": "ACTIVE",
            "source_style": "gws", "direction": "SELL",
            "entry_low": 3997, "entry_high": 4003,
            "current_sl": 4005, "sl_mode": "PRICE",
            "tps": {"1": 3995, "2": 3994, "3": 3993, "4": 3990},
            "opened_at": (self.now - timedelta(seconds=80)).isoformat(),
            "last_update_at": (self.now - timedelta(seconds=10)).isoformat(),
            "history": [{"kind": "partial_close", "message_id": 201,
                         "partial_percent": 25}],
        }

    def feed(self, v5=True):
        import json
        self.path.write_text(json.dumps({"signals":[self.signal]}))
        handler = FeedRecorder(self.config)
        handler._demo_v2_feed(v5=v5)
        return handler.body.splitlines()

    def test_unstable_row_kept_for_management(self):
        lines = self.feed()
        self.assertIn("|CANCELLED|SELL|", lines[1])
        self.assertTrue(any(line.startswith("A|") for line in lines[2:]))

    def test_stable_four_targets_active(self):
        self.signal["last_update_at"] = (self.now-timedelta(seconds=55)).isoformat()
        self.assertIn("|ACTIVE|SELL|", self.feed()[1])

    def test_single_tp_sub_one_r_held(self):
        self.signal["tps"] = {"1":3995}
        self.signal["last_update_at"] = (self.now-timedelta(seconds=55)).isoformat()
        self.assertIn("|CANCELLED|SELL|", self.feed()[1])

    def test_single_tp_at_one_r_allowed(self):
        self.signal["tps"] = {"1":3989}
        self.signal["last_update_at"] = (self.now-timedelta(seconds=55)).isoformat()
        self.assertIn("|ACTIVE|SELL|", self.feed()[1])

    def test_non_gws_unchanged(self):
        self.signal["source_style"] = "elite"
        self.assertIn("|ACTIVE|SELL|", self.feed()[1])

    def test_legacy_feed_unchanged(self):
        self.assertIn("|ACTIVE|SELL|", self.feed(v5=False)[1])


if __name__ == "__main__":
    unittest.main()
