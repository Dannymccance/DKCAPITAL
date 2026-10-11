from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler


class DemoFeedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "signals.json"
        self.token = "test-demo-token-longer-than-24-characters"
        with patch.dict(os.environ, {
            "MT5_GATEWAY_TOKEN": self.token,
            "MT5_DEMO_FEED_ENABLED": "true",
            "SIGNAL_STATE_PATH": str(self.path),
        }):
            config = GatewayConfig()
        config.validate()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mt5GatewayHandler)
        self.server.config = config
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def request(self, token: str | None = None):
        url = f"http://127.0.0.1:{self.server.server_port}/v1/demo/feed"
        headers = {"Authorization": "Bearer " + token} if token is not None else {}
        return urlopen(Request(url, headers=headers), timeout=3)

    def test_authentication_required(self):
        with self.assertRaises(HTTPError) as exc:
            self.request(None)
        self.assertEqual(exc.exception.code, 401)

    def test_http11_framing_and_explicit_close(self):
        with self.request(self.token) as response:
            self.assertEqual(response.version, 11)
            self.assertEqual(response.headers.get("Connection"), "close")
            body = response.read()
            self.assertEqual(int(response.headers["Content-Length"]), len(body))
            self.assertTrue(body.startswith(b"DKDEMO1"))

    def test_feed_contains_expected_13_fields(self):
        self.path.write_text(json.dumps({
            "signals": [{
                "symbol": "XAUUSD", "signal_id": "-10077:43",
                "status": "ACTIVE", "direction": "BUY",
                "entry_low": 4105, "entry_high": 4108, "current_sl": 4100,
                "sl_mode": "PRICE", "tps": {"1": 4112, "2": 4114.5, "3": 4146.5},
                "opened_at": datetime.now(UTC).isoformat(),
                "remaining_fraction": 1.0,
            }]
        }), encoding="utf-8")
        with self.request(self.token) as response:
            lines = response.read().decode("utf-8").splitlines()
        self.assertEqual(lines[0], "DKDEMO1")
        fields = lines[1].split("|")
        self.assertEqual(len(fields), 13)
        self.assertEqual(fields[:4], ["S", "-10077:43", "ACTIVE", "BUY"])
        self.assertEqual(fields[8:11], ["4112.0", "4114.5", "4146.5"])

    def test_injected_ids_rejected(self):
        self.path.write_text(json.dumps({"signals": [{
            "symbol": "XAUUSD", "signal_id": "malicious\nS|",
            "status": "ACTIVE", "direction": "BUY",
            "entry_low": 4105, "entry_high": 4108,
            "opened_at": datetime.now(UTC).isoformat(),
        }]}), encoding="utf-8")
        with self.request(self.token) as response:
            self.assertEqual(response.read().decode("utf-8"), "DKDEMO1\n")


if __name__ == "__main__":
    unittest.main()
