"""Dan-only MT5 cutover and MQL risk scheduling contract tests.

These tests do not assert that MQL was broker-compiled or trade-tested.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler
from dkcapital.mt5_profile_registry import profiles_from_registry
from scripts.generate_mt5_v211_profile import generate


class DanRiskTests(unittest.TestCase):
    def test_account3_uses_broker_stop_risk_budget_not_fixed_balance_lot_multiplier(self):
        dan = generate(3, 710003, "REAL", "USD", "Vantage-Live", 130)
        self.assertIn("int UKTradingHour()", dan)
        self.assertIn("int hour=UKTradingHour();", dan)
        self.assertIn("return hour>=1 && hour<9 ? 8.0 : 2.0;", dan)
        self.assertIn("return RiskBalanceAccountCurrency()*SessionRiskPct()/100.0;", dan)
        self.assertEqual(dan.count("return RiskBalanceAccountCurrency()*SessionRiskPct()/100.0;"), 2)
        self.assertIn("highRisk?MathMin(SessionRiskPct(),HighRiskSignalPct):SessionRiskPct()", dan)
        self.assertIn("const double INITIAL_GROUP_RISK_SHARE = 1.0;", dan)
        self.assertIn("probeLoss/probeLot", dan)
        self.assertNotIn("MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/LotDenominatorAccountCurrency)*0.01", dan)
        self.assertIn("MathMin(balance,MathMax(0.0,equity-credit))", dan)
        self.assertIn("input bool EnableNewEntries = false;", dan)
        self.assertIn("MaxSpreadUSD", dan)
        self.assertIn('RecordDecision(id,"ENTRY","BROKER_EXPOSURE_UNSAFE",age)', dan)
        self.assertIn("provider stop widening held", dan)

    def test_legacy_template_not_accidentally_rewritten(self):
        kyle_legacy = generate(2, 710002, "REAL", "USD", "Vantage-Live", 130)
        self.assertNotIn("int UKTradingHour()", kyle_legacy)
        self.assertIn("const double INITIAL_GROUP_RISK_SHARE = 0.85;", kyle_legacy)

    def test_uk_clock_boundary_expectations_are_documented(self):
        # Independent reference for the EA's final-Sunday UTC 01:00 switch.
        london = ZoneInfo("Europe/London")
        cases = [
            ("2026-03-29T00:59:00+00:00", 0, 2),
            ("2026-03-29T01:00:00+00:00", 2, 8),
            ("2026-10-25T00:59:00+00:00", 1, 8),
            ("2026-10-25T01:00:00+00:00", 1, 8),
            ("2026-10-27T00:59:59+00:00", 0, 2),
            ("2026-10-27T01:00:00+00:00", 1, 8),
            ("2026-10-27T08:59:59+00:00", 8, 8),
            ("2026-10-27T09:00:00+00:00", 9, 2),
            ("2026-07-01T08:00:00+00:00", 9, 2),
        ]
        for iso, local_hour, risk in cases:
            with self.subTest(iso=iso):
                hour = datetime.fromisoformat(iso).astimezone(london).hour
                self.assertEqual(hour, local_hour)
                self.assertEqual(8 if 1 <= hour < 9 else 2, risk)


class DanOnlySwitchTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "MT5_DAN_ONLY": "true",
            "MT5_GATEWAY_TOKEN": "gateway-token-strong-24-chars",
            "MT5_ACCOUNT2_LOGIN": "710002",
            "MT5_ACCOUNT2_MODE": "REAL",
            "MT5_ACCOUNT2_CURRENCY": "USD",
            "MT5_ACCOUNT2_SERVER": "Vantage-Live",
            "MT5_ACCOUNT2_TOKEN": "account2-token-strong-24-chars",
            "MT5_ACCOUNT3_LOGIN": "710003",
            "MT5_ACCOUNT3_MODE": "REAL",
            "MT5_ACCOUNT3_CURRENCY": "USD",
            "MT5_ACCOUNT3_SERVER": "Vantage-Live",
            "MT5_ACCOUNT3_TOKEN": "account3-token-strong-24-chars",
        }

    def test_only_dan_is_commissioned_in_gateway(self):
        with patch.dict(os.environ, self.env, clear=True):
            config = GatewayConfig()
            config.validate()
            self.assertEqual(set(config.profiles), {3})
            self.assertTrue(config.dan_only)
        with patch.dict(os.environ, {**self.env, "MT5_ACCOUNT3_MODE": "DEMO"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Account 3"):
                GatewayConfig().validate()
        with patch.dict(os.environ, {k: v for k, v in self.env.items()
                                      if not k.startswith("MT5_ACCOUNT3_")}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Account 3"):
                GatewayConfig().validate()

    def test_legacy_trade_feeds_and_telemetry_are_gone(self):
        handler = Mt5GatewayHandler.__new__(Mt5GatewayHandler)
        handler.server = SimpleNamespace(config=SimpleNamespace(dan_only=True))
        responses = []
        handler._write_json = lambda code, payload: responses.append((int(code), payload))
        for url in ("/v1/demo/feed", "/v5/demo/feed", "/v5/accounts/1/quality",
                    "/v2/demo/telemetry", "/v5/accounts/2/feed", "/v5/accounts/2/telemetry"):
            with self.subTest(url=url):
                handler.path = url
                self.assertTrue(handler._retired_route())
                self.assertEqual(responses[-1][0], 410)
                self.assertEqual(responses[-1][1]["active_account"], 3)
        handler.path = "/v5/accounts/3/feed"
        self.assertFalse(handler._retired_route())

    def test_registry_monitors_dan_only(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            payload = {"accounts": [
                {"id": 1, "enabled": True, "label": "Paper"},
                {"id": 2, "enabled": True, "label": "Kyle"},
                {"id": 3, "enabled": True, "label": "Dan",
                 "expected_mode": "REAL", "expected_currency": "USD",
                 "expected_login": 710003, "expect_entries": True}
            ]}
            (folder / "mt5-monitor-profiles.json").write_text(json.dumps(payload))
            with patch.dict(os.environ, {"MT5_DAN_ONLY": "true"}):
                profiles, valid = profiles_from_registry(folder)
                self.assertTrue(valid)
                self.assertEqual([p["id"] for p in profiles], [3])
                (folder / "mt5-monitor-profiles.json").unlink()
                self.assertEqual(profiles_from_registry(folder), ([], False))


if __name__ == "__main__":
    unittest.main()
