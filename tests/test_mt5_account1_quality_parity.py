"""Account 1 quality endpoint parity without modifying MT5 feeds or EA."""
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


class Account1QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.snapshot_path = root / "telemetry.json"
        self.signal_path = root / "signals.json"
        self.token = "test-account1-gateway-token-123456789"
        with patch.dict(os.environ, {
            "MT5_GATEWAY_TOKEN": self.token,
            "MT5_ACCOUNT1_LOGIN": "5057220041",
            "MT5_ACCOUNT1_TELEMETRY_PATH": str(self.snapshot_path),
            "SIGNAL_STATE_PATH": str(self.signal_path),
        }):
            config = GatewayConfig()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mt5GatewayHandler)
        self.server.config = config
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v5/accounts/1/quality"
        self.snapshot_path.write_text(json.dumps({
            "version": 2, "login": 5057220041, "mode": "DEMO",
            "currency": "GBP", "received_at": datetime.now(UTC).isoformat(),
            "positions": [],
        }))
        self.signal_path.write_text('{"signals": []}')

    def get(self, token=None):
        return urlopen(Request(self.url, headers={
            "Authorization": "Bearer " + (self.token if token is None else token)
        }), timeout=3)

    def test_account1_quality_verified(self):
        with self.get() as response:
            body = json.load(response)
        self.assertTrue(body["verified"])
        self.assertEqual(body["warnings"], [])

    def test_rejects_invalid_token(self):
        with self.assertRaises(HTTPError) as error:
            self.get("invalid-token")
        self.assertEqual(error.exception.code, 401)

    def test_wrong_login_cannot_report_quality(self):
        data = json.loads(self.snapshot_path.read_text())
        data["login"] = 999
        self.snapshot_path.write_text(json.dumps(data))
        with self.assertRaises(HTTPError) as error:
            self.get()
        self.assertEqual(error.exception.code, 503)

    def test_existing_account1_snapshot_still_available(self):
        url = f"http://127.0.0.1:{self.server.server_port}/v2/demo/account1"
        with urlopen(Request(url, headers={
            "Authorization": "Bearer " + self.token
        }), timeout=3) as response:
            body = json.load(response)
        self.assertEqual(body["login"], 5057220041)


if __name__ == "__main__":
    unittest.main()
