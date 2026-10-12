from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dkcapital.mt5_account_reporting import normalize_telemetry, summarize
from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler

LOGIN = 5057220041
MAGIC = 5100000000 + 12345 * 10


def example():
    when = datetime.now(UTC).isoformat()
    return {
        "version": 2, "mode": "DEMO", "login": LOGIN,
        "currency": "GBP", "generated_at": when,
        "balance": 1000, "equity": 1025, "margin": 80,
        "free_margin": 945, "bid": 4101, "ask": 4101.2,
        "positions": [
            {"ticket": 1, "magic": MAGIC + 2, "side": "BUY", "lots": 0.03,
             "entry": 4100, "sl": 4100, "tp": 4110,
             "profit": 3, "swap": 0, "risk_gbp": 1.5},
        ],
        "deals": [
            {"ticket": 101, "position_id": 11, "magic": MAGIC + 1,
             "entry_type": "IN", "side": "BUY", "lots": 0.03, "price": 4100,
             "profit": 0, "commission": -0.10, "swap": 0, "time": when},
            {"ticket": 102, "position_id": 12, "magic": MAGIC + 2,
             "entry_type": "IN", "side": "BUY", "lots": 0.03, "price": 4100,
             "profit": 0, "commission": -0.10, "swap": 0, "time": when},
            {"ticket": 103, "position_id": 13, "magic": MAGIC + 3,
             "entry_type": "IN", "side": "BUY", "lots": 0.04, "price": 4100,
             "profit": 0, "commission": -0.10, "swap": 0, "time": when},
            {"ticket": 104, "position_id": 11, "magic": MAGIC + 1,
             "entry_type": "OUT", "side": "SELL", "lots": 0.03, "price": 4101,
             "profit": 2.30, "commission": -0.10, "swap": 0, "time": when},
        ],
    }


class ReportingUnitTests(unittest.TestCase):
    def test_rejects_wrong_login_and_live(self):
        data = example()
        with self.assertRaises(ValueError):
            normalize_telemetry({**data, "mode": "LIVE"}, LOGIN)
        with self.assertRaises(ValueError):
            normalize_telemetry(data, LOGIN + 1)
        with self.assertRaises(ValueError):
            normalize_telemetry({**data, "currency": "USD"}, LOGIN)

    def test_requires_fresh_utc_and_native_tickets(self):
        data = example()
        data["generated_at"] = (datetime.now(UTC)-timedelta(minutes=4)).isoformat()
        with self.assertRaises(ValueError):
            normalize_telemetry(data, LOGIN)
        data = example()
        data["positions"][0]["magic"] = 0
        with self.assertRaises(ValueError):
            normalize_telemetry(data, LOGIN)

    def test_weekly_pips_counts_each_signal_once(self):
        snapshot = normalize_telemetry(example(), LOGIN)
        result = summarize(snapshot)
        # A single closed leg's $1 move counts once per signal.
        self.assertEqual(result["week_realized_pips"], 100.0)
        self.assertEqual(result["week_closed_signals"], 1)
        self.assertEqual(result["week_realized_gbp"], 1.9)
        self.assertEqual(result["floating_gbp"], 25)
        self.assertEqual(result["portfolio_risk_gbp"], 1.5)
        self.assertEqual(result["positions"][0]["floating_pips"], 100.0)

    def test_accepts_fourth_layer_as_owned_broker_position(self):
        data = example()
        when = datetime.now(UTC).isoformat()
        data["positions"].append({
            "ticket": 4, "magic": MAGIC + 4, "side": "BUY",
            "lots": 0.01, "entry": 4099.0, "sl": 4090.0,
            "tp": 4110.0, "profit": 2.0, "swap": 0.0, "risk_gbp": 3.0,
        })
        data["deals"].append({
            "ticket": 105, "position_id": 14, "magic": MAGIC + 4,
            "entry_type": "IN", "side": "BUY", "lots": 0.01,
            "price": 4099.0, "profit": 0.0, "commission": -0.10,
            "swap": 0.0, "time": when,
        })
        snapshot = normalize_telemetry(data, LOGIN)
        summary = summarize(snapshot)
        self.assertEqual(snapshot["positions"][-1]["leg"], 4)
        self.assertEqual(snapshot["deals"][-1]["magic"], MAGIC + 4)
        self.assertEqual(summary["portfolio_risk_gbp"], 4.5)
        self.assertEqual(summary["week_closed_signals"], 1)

    def test_rejects_unowned_fifth_leg(self):
        data = example()
        data["positions"][0]["magic"] = MAGIC + 5
        with self.assertRaises(ValueError):
            normalize_telemetry(data, LOGIN)
        data = example()
        data["deals"][0]["magic"] = MAGIC + 5
        with self.assertRaises(ValueError):
            normalize_telemetry(data, LOGIN)

    def test_missing_stop_marked_unknown(self):
        data = example()
        data["positions"][0]["sl"] = 0
        data["positions"][0]["risk_gbp"] = None
        output = summarize(normalize_telemetry(data, LOGIN))
        self.assertTrue(output["risk_unknown"])
        self.assertIsNone(output["portfolio_risk_gbp"])

    def test_week_start_and_no_prior_week_credit(self):
        data = example()
        old = (datetime.now(UTC)-timedelta(days=10)).isoformat()
        data["deals"][3]["time"] = old
        report = summarize(normalize_telemetry(data, LOGIN))
        self.assertEqual(report["week_realized_pips"], 0)


class GatewayTelemetryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "state.json"
        self.telemetry_path = Path(tmp.name) / "account1.json"
        self.token = "unit-test-token-that-is-long-enough"
        with patch.dict(os.environ, {
            "MT5_GATEWAY_TOKEN": self.token, "MT5_DEMO_FEED_ENABLED": "true",
            "MT5_ACCOUNT1_LOGIN": str(LOGIN),
            "MT5_ACCOUNT1_TELEMETRY_PATH": str(self.telemetry_path),
            "SIGNAL_STATE_PATH": str(self.path),
        }):
            cfg = GatewayConfig()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Mt5GatewayHandler)
        self.server.config = cfg
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def call(self, route, *, method="GET", obj=None, token=None):
        token = self.token if token is None else token
        data = json.dumps(obj).encode() if obj is not None else None
        req = Request(f"http://127.0.0.1:{self.server.server_port}{route}",
                      data=data, method=method,
                      headers={"Authorization": "Bearer " + token,
                               "Content-Type": "application/json"})
        return urlopen(req, timeout=3)

    def test_telemetry_auth_and_account_lock(self):
        with self.assertRaises(HTTPError) as exc:
            self.call("/v2/demo/telemetry", method="POST",
                      obj=example(), token="wrong")
        self.assertEqual(exc.exception.code, 401)
        with self.assertRaises(HTTPError) as exc:
            self.call("/v2/demo/telemetry", method="POST",
                      obj={**example(), "mode": "LIVE"})
        self.assertEqual(exc.exception.code, 400)
        self.assertFalse(self.telemetry_path.exists())
        with self.call("/v2/demo/telemetry", method="POST", obj=example()) as r:
            self.assertTrue(json.load(r)["accepted"])
        with self.call("/v2/demo/account1") as r:
            self.assertEqual(json.load(r)["login"], LOGIN)

    def test_feed_encodes_layer_permission_as_nonexecuting_instruction(self):
        self.path.write_text(json.dumps({"signals": [{
            "symbol": "XAUUSD", "signal_id": "-10011:222",
            "status": "ACTIVE", "direction": "BUY",
            "entry_low": 4150, "entry_high": 4153,
            "current_sl": 4140, "sl_mode": "PRICE",
            "source_style": "gws",
            "tps": {"1": 4160}, "layer_max": 4145,
            "opened_at": datetime.now(UTC).isoformat(),
            "history": [
                {"kind": "opened", "message_id": 222},
                {"kind": "trade_update", "message_id": 223, "layer_max": 4145},
                {"kind": "add_layer", "message_id": 224,
                 "timestamp": datetime.now(UTC).isoformat(), "layer_max": 4145},
            ],
        }]}))
        with self.call("/v2/demo/feed") as response:
            lines = response.read().decode().splitlines()
        self.assertEqual(
            len([line for line in lines if line.startswith("L|-10011:222|224|10|1|4145.0|")]), 1
        )
        self.assertFalse(any(line.startswith("A|-10011:222|224|") for line in lines))


    def test_permission_alone_does_not_emit_layer_order(self):
        self.path.write_text(json.dumps({"signals": [{
            "symbol": "XAUUSD", "signal_id": "-10011:222",
            "source_style": "gws", "status": "ACTIVE", "direction": "BUY",
            "entry_low": 4150, "entry_high": 4153,
            "current_sl": 4140, "sl_mode": "PRICE",
            "tps": {"1": 4160, "2": 4162.5}, "layer_max": 4145,
            "opened_at": datetime.now(UTC).isoformat(),
            "history": [{"kind": "trade_update", "message_id": 223, "layer_max": 4145}],
        }]}))
        with self.call("/v2/demo/feed") as response:
            lines = response.read().decode().splitlines()
        self.assertFalse(any(line.startswith("L|") for line in lines))

    def test_old_or_unattributed_add_never_emitted(self):
        now = datetime.now(UTC)
        for source_style, timestamp in [
            ("elite", now.isoformat()), ("gws", None)
        ]:
            with self.subTest(style=source_style, timestamp=timestamp):
                self.path.write_text(json.dumps({"signals": [{
                    "symbol": "XAUUSD", "signal_id": "-10011:222",
                    "source_style": source_style,
                    "status": "ACTIVE", "direction": "BUY",
                    "entry_low": 4150, "entry_high": 4153,
                    "current_sl": 4140, "sl_mode": "PRICE",
                    "opened_at": now.isoformat(),
                    "history": [{"kind": "add_layer", "message_id": 224,
                                 "timestamp": timestamp}],
                }]}))
                with self.call("/v2/demo/feed") as response:
                    lines = response.read().decode().splitlines()
                self.assertFalse(any(line.startswith("L|") for line in lines))

    def test_feed_exposes_only_explicit_provider_close(self):
        self.path.write_text(json.dumps({"signals":[{
            "symbol":"XAUUSD","signal_id":"-10011:222","status":"COMPLETED",
            "direction":"BUY","entry_low":4100,"entry_high":4101,
            "current_sl":4090,"sl_mode":"PRICE",
            "tps":{"1":4102,"2":4105,"3":4110},
            "opened_at":(datetime.now(UTC)-timedelta(days=3)).isoformat(),
            "history":[
                {"kind":"opened","message_id":222},
                {"kind":"trade_update","message_id":223,"tp_hits":[1]},
                {"kind":"partial_close","message_id":224,"partial_percent":40},
                {"kind":"close","message_id":225},
            ],
        }]}))
        with self.call("/v2/demo/feed") as r:
            lines = r.read().decode().splitlines()
        self.assertEqual(lines[0], "DKDEMO2")
        self.assertTrue(any(l.startswith("S|-10011:222|COMPLETED|") for l in lines))
        self.assertIn("A|-10011:222|224|PARTIAL_CLOSE|40.0", lines)
        self.assertIn("A|-10011:222|225|CLOSE|0.0", lines)
        self.assertFalse(any("|223|" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
