"""Independent MT5 profile EA generation and account-isolated gateway tests."""
from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dkcapital.mt5_account_reporting import normalize_telemetry
from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler
from scripts.generate_mt5_v211_profile import generate


class ProfileBuildTests(unittest.TestCase):
    def test_three_distinct_account_bound_sources_are_safe_by_default(self):
        kyle = generate(2, 72300402, "DEMO", "GBP", "Broker-Demo", 100.0)
        dan = generate(3, 72300403, "REAL", "USD", "Vantage-Live", 130.0)
        for index, candidate, mode, ccy, server in (
            (2, kyle, "DEMO", "GBP", "Broker-Demo"),
            (3, dan, "REAL", "USD", "Vantage-Live"),
        ):
            self.assertIn(f"COMPILED_ACCOUNT_ID = {index}", candidate)
            self.assertIn(f"DKACCOUNT{index}V5", candidate)
            self.assertIn(f"/v5/accounts/{index}/feed", candidate)
            self.assertIn(f"/v5/accounts/{index}/telemetry", candidate)
            self.assertIn(f'AccountInfoString(ACCOUNT_SERVER)=="{server}"', candidate)
            self.assertIn(f'ACCOUNT_TRADE_MODE_{mode}', candidate)
            self.assertIn(f'AccountInfoString(ACCOUNT_CURRENCY)=="{ccy}"', candidate)
            self.assertIn("input bool EnableNewEntries = false;", candidate)
            self.assertIn("input double HighRiskSignalPct = 2.0;", candidate)
            self.assertIn("AccountInfoDouble(ACCOUNT_CREDIT)", candidate)
            self.assertIn("RiskAccountCurrency(", candidate)
            self.assertIn("for(int i=0;i<initialLegCount;i++)", candidate)
            self.assertIn('GlobalVariableSet(IDKey(id,"TARGET_COUNT"),targetCount);', candidate)
            self.assertNotIn("AccountInfoString(ACCOUNT_CURRENCY)==\"GBP\";"
                             if ccy == "USD" else "UNLIKELY_MARKER", candidate)
        self.assertNotEqual(kyle, dan)

    def test_missing_identity_and_unapproved_lot_policy_rejected(self):
        args = (2, 77700001, "DEMO", "GBP", "Broker-Demo", 100.0)
        for modified in (
            (1, *args[1:]), (3, 5057220041, *args[2:]),
            (2, 0, *args[2:]), (2, 77700001, "REAL", "EUR", "Broker", 100),
            (2, 77700001, "REAL", "USD", "abc;cmd", 100),
            (2, 77700001, "REAL", "USD", "Broker", 0),
        ):
            with self.subTest(modified=modified):
                with self.assertRaises(ValueError):
                    generate(*modified)

    def test_no_compiled_binary_or_secret_generated(self):
        source = generate(3, 72999993, "REAL", "USD", "Vantage-Live", 120)
        self.assertNotIn("AUTHORIZATION: Bearer", source.upper())
        self.assertIn('input string FeedToken = "";', source)
        self.assertIn("ACCOUNT_TRADE_MODE_REAL", source)
        self.assertIn("HighRiskSignalPct<=0 || HighRiskSignalPct>MaxSignalRiskPct", source)


class ProfileGatewayTests(unittest.TestCase):
    def test_strict_profile_configuration_and_token_separation(self):
        env = {
            "MT5_GATEWAY_TOKEN": "account1-separate-token-more-than-24",
            "MT5_ACCOUNT1_LOGIN": "5057220041",
            "MT5_ACCOUNT2_LOGIN": "725000002",
            "MT5_ACCOUNT2_MODE": "DEMO",
            "MT5_ACCOUNT2_CURRENCY": "GBP",
            "MT5_ACCOUNT2_SERVER": "Broker-Demo",
            "MT5_ACCOUNT2_TOKEN": "account2-distinct-token-more-than-24",
            "MT5_ACCOUNT3_LOGIN": "725000003",
            "MT5_ACCOUNT3_MODE": "REAL",
            "MT5_ACCOUNT3_CURRENCY": "USD",
            "MT5_ACCOUNT3_SERVER": "Vantage-Live",
            "MT5_ACCOUNT3_TOKEN": "account3-distinct-token-more-than-24",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = GatewayConfig()
            cfg.validate()
            self.assertEqual(cfg.profiles[2]["login"], 725000002)
            self.assertEqual(cfg.profiles[3]["mode"], "REAL")
            self.assertNotEqual(cfg.profiles[2]["token"], cfg.profiles[3]["token"])
        with patch.dict(os.environ, {**env, "MT5_ACCOUNT3_LOGIN": "725000002"}, clear=True):
            with self.assertRaises(RuntimeError):
                GatewayConfig().validate()
        with patch.dict(os.environ, {**env, "MT5_ACCOUNT3_TOKEN": env["MT5_ACCOUNT2_TOKEN"]}, clear=True):
            with self.assertRaises(RuntimeError):
                GatewayConfig().validate()
        with patch.dict(os.environ, {"MT5_GATEWAY_TOKEN": env["MT5_GATEWAY_TOKEN"],
                                     "MT5_ACCOUNT2_LOGIN": "725000002"}, clear=True):
            with self.assertRaises(RuntimeError):
                GatewayConfig()

    def test_profile_authentication_and_header_separation(self):
        profile = {
            "login": 725000003, "mode": "REAL", "currency": "USD",
            "server": "Vantage-Live", "token": "separate-profile-token-over-24",
            "telemetry_path": Path("/somewhere/not-present"),
        }
        cfg = SimpleNamespace(profiles={3: profile}, token="demo-token-over-24",
                              demo_feed_enabled=True, signal_state_path=Path("/nope"))
        handler = Mt5GatewayHandler.__new__(Mt5GatewayHandler)
        handler.server = SimpleNamespace(config=cfg)
        writes = []
        handler._write_text = lambda code, body: writes.append((code, body))
        handler._write_json = lambda code, payload: writes.append((code, payload))
        handler.headers = {"Authorization": "Bearer " + profile["token"]}
        handler.path = "/v5/accounts/3/feed"
        with patch("dkcapital.mt5_gateway._json", return_value={"signals": []}):
            handler.do_GET()
        self.assertEqual(writes[-1][1], "DKACCOUNT3V5\n")
        handler.headers = {"Authorization": "Bearer demo-token-over-24"}
        handler.do_GET()
        self.assertEqual(writes[-1][1]["error"], "unauthorized")

    def test_normalized_profile_identity_and_sl_widening_alert(self):
        payload = {
            "version": 2, "mode": "DEMO", "currency": "GBP",
            "login": 5057220041, "generated_at": datetime.now(UTC).isoformat(),
            "balance": 1000.0, "equity": 1025.0,
            "margin": 100.0, "free_margin": 925.0,
            "bid": 4010.0, "ask": 4010.2,
            "positions": [], "deals": [],
        }
        payload["login"] = 725000003
        payload["mode"] = "REAL"
        payload["currency"] = "USD"
        payload["server"] = "Vantage-Live"
        payload["decisions"] = [{
            "signal_id": "123:45", "stage": "STOP", "code": "PROVIDER_SL_WIDENING_EXCEEDS_RISK",
            "at": datetime.now(UTC).isoformat(), "bid": 4000, "ask": 4001,
            "age_seconds": 0, "broker_retcode": 0,
        }]
        output = normalize_telemetry(payload, 725000003,
                                     expected_mode="REAL", expected_currency="USD",
                                     expected_server="Vantage-Live")
        self.assertEqual(output["mode"], "REAL")
        self.assertEqual(output["currency"], "USD")
        self.assertEqual(output["decisions"][0]["stage"], "STOP")
        for changes in ({"login": 725000004}, {"mode": "DEMO"},
                        {"currency": "GBP"}, {"server": "Wrong-Broker"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    normalize_telemetry({**payload, **changes}, 725000003,
                                        expected_mode="REAL", expected_currency="USD",
                                        expected_server="Vantage-Live")


if __name__ == "__main__":
    unittest.main()
