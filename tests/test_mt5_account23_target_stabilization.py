"""Account-specific target stabilization regression tests.

Run with: python -m unittest tests.test_mt5_account23_target_stabilization
"""
import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler


class Account23TargetStabilizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "state.json"
        self.account_token = "account-two-private-test-token-123456789"
        self.demo_token = "demo-private-test-token-1234567890123"
        with patch.dict(os.environ, {
            "MT5_GATEWAY_TOKEN": self.demo_token,
            "MT5_DEMO_FEED_ENABLED": "true",
            "SIGNAL_STATE_PATH": str(self.path),
            "MT5_ACCOUNT2_LOGIN": "500002",
            "MT5_ACCOUNT2_MODE": "DEMO",
            "MT5_ACCOUNT2_CURRENCY": "USD",
            "MT5_ACCOUNT2_SERVER": "Test-Broker-Demo",
            "MT5_ACCOUNT2_TOKEN": self.account_token,
        }):
            config = GatewayConfig()
        config.validate()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mt5GatewayHandler)
        self.server.config = config
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def feed(self, profile=True):
        route = "/v5/accounts/2/feed" if profile else "/v5/demo/feed"
        token = self.account_token if profile else self.demo_token
        url = f"http://127.0.0.1:{self.server.server_port}{route}"
        with urlopen(Request(url, headers={"Authorization": "Bearer " + token}), timeout=3) as response:
            return response.read().decode().splitlines()

    def signal(self, targets, *, age_seconds=10, source="gws"):
        self.path.write_text(json.dumps({"signals": [{
            "symbol": "XAUUSD", "signal_id": "-1000:321",
            "status": "ACTIVE", "direction": "SELL",
            "entry_low": 3997, "entry_high": 4003,
            "current_sl": 4005, "sl_mode": "PRICE",
            "tps": {str(i+1): value for i, value in enumerate(targets)},
            "opened_at": (datetime.now(UTC) - timedelta(seconds=age_seconds)).isoformat(),
            "source_style": source, "history": [],
        }]}))

    def test_fresh_gws_single_target_held_on_account2_only(self):
        self.signal([3995])
        self.assertEqual(self.feed(), ["DKACCOUNT2V5"])
        self.assertEqual(len(self.feed(profile=False)), 2)  # Account 1 unchanged.

    def test_edit_to_four_targets_releases_immediately(self):
        self.signal([3995])
        self.assertEqual(len(self.feed()), 1)
        self.signal([3995, 3994, 3993, 3990])
        lines = self.feed()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1].split("|")[8:14],
                         ["3995.0", "3994.0", "3993.0", "3990.0", "0.0", "0.0"])

    def test_genuine_single_target_releases_after_stabilization(self):
        self.signal([3995], age_seconds=100)
        self.assertEqual(len(self.feed()), 2)

    def test_two_target_gws_signal_not_blocked(self):
        self.signal([3995, 3990])
        self.assertEqual(len(self.feed()), 2)

    def test_other_provider_unaffected(self):
        self.signal([3995], source="elite")
        self.assertEqual(len(self.feed()), 2)


if __name__ == "__main__":
    unittest.main()
