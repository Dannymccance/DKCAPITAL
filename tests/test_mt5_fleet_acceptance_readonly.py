"""Deterministic offline tests: no broker calls or live account changes."""
from __future__ import annotations
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from scripts.mt5_fleet_acceptance_readonly import assess, private_values, validate_snapshot


class AcceptanceTests(unittest.TestCase):
    def config(self):
        return {
            "MT5_GATEWAY_TOKEN": "demo-" + "a"*60,
            "MT5_ACCOUNT1_LOGIN": "5057220041",
            "MT5_ACCOUNT2_LOGIN": "11223344",
            "MT5_ACCOUNT2_MODE": "REAL",
            "MT5_ACCOUNT2_CURRENCY": "USD",
            "MT5_ACCOUNT2_SERVER": "Broker-Live",
            "MT5_ACCOUNT2_TOKEN": "kyle-" + "b"*60,
            "MT5_ACCOUNT3_LOGIN": "36193223",
            "MT5_ACCOUNT3_MODE": "REAL",
            "MT5_ACCOUNT3_CURRENCY": "USD",
            "MT5_ACCOUNT3_SERVER": "VantageMarkets-Live 4",
            "MT5_ACCOUNT3_TOKEN": "dan-" + "c"*60,
        }

    def snapshot(self, n):
        env = self.config()
        return {
            "received_at": datetime.now(UTC).isoformat(),
            "login": int(env["MT5_ACCOUNT1_LOGIN" if n == 1 else f"MT5_ACCOUNT{n}_LOGIN"]),
            "mode": "DEMO" if n == 1 else "REAL",
            "currency": "GBP" if n == 1 else "USD",
            "server": None if n == 1 else env[f"MT5_ACCOUNT{n}_SERVER"],
            "feed_http": 200, "entry_enabled": n != 2,
            "broker_positions_total": 0, "broker_orders_total": 0,
            "broker_positions_protected": True,
            "signal_risk_cap_account_currency": 40, "balance": 1000,
            "signal_risk_cap_pct": 4,
            "daily_loss_warning_pct": 2, "daily_loss_guard_mode": "ALERT_ONLY",
        }

    def test_all_three_pass_without_touching_live_mt5(self):
        env = self.config()
        def respond(path, token):
            n = 1 if path.startswith("/v5/demo") or path.endswith("/account1") else int(path.split("/")[3])
            own = env["MT5_GATEWAY_TOKEN" if n == 1 else f"MT5_ACCOUNT{n}_TOKEN"]
            if token != own:
                return 401, b'{"error":"unauthorized"}'
            if path.endswith("/feed"):
                header = "DKDEMO5" if n == 1 else f"DKACCOUNT{n}V5"
                return 200, (header + "\n").encode()
            return 200, json.dumps(self.snapshot(n)).encode()
        with patch("scripts.mt5_fleet_acceptance_readonly.read_url", side_effect=respond), \
             patch("scripts.mt5_fleet_acceptance_readonly.is_active", return_value=True):
            report = assess(env)
        self.assertEqual(report["overall"], "PASS", report)
        self.assertEqual(report["accounts"]["2"]["entry_enabled"], False)
        self.assertEqual(report["accounts"]["3"]["other_account_token_http"], 401)

    def test_risk_stops_account_identity_and_freshness_fail(self):
        env = self.config()
        snapshot = self.snapshot(2)
        snapshot.update({
            "login": 88,
            "server": "other",
            "signal_risk_cap_account_currency": 150,
            "broker_positions_protected": False,
            "received_at": (datetime.now(UTC) - timedelta(seconds=300)).isoformat(),
        })
        errors = validate_snapshot(snapshot, 2, env, now=datetime.now(UTC))
        self.assertIn("wrong login", errors)
        self.assertIn("wrong broker server", errors)
        self.assertIn("stale telemetry", errors)
        self.assertIn("broker-native stops not verified", errors)
        self.assertIn("risk cap exceeds reported cash-balance percentage", errors)

    def test_duplicate_or_reused_secrets_rejected_without_printing(self):
        env = self.config()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".env"
            path.write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
            self.assertEqual(private_values(path), env)
            with path.open("a") as writer:
                writer.write("MT5_ACCOUNT2_LOGIN=88\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                private_values(path)
            path.write_text("\n".join(f"{k}={v}" for k, v in {
                **env, "MT5_ACCOUNT2_TOKEN": env["MT5_ACCOUNT3_TOKEN"]
            }.items()))
            with self.assertRaisesRegex(ValueError, "duplicate independent gateway tokens"):
                private_values(path)

if __name__ == "__main__":
    unittest.main()
