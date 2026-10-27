"""Verify Kyle commissions from private identity only, using common V2.11 rules."""
from __future__ import annotations
import tempfile
import unittest
from pathlib import Path
from scripts.prepare_mt5_account2_private import prepare, private_identity
from scripts.generate_mt5_v211_profile import generate


class KyleOnboardingTests(unittest.TestCase):
    def write_env(self, directory, *, login="778811", token="abcd" * 16):
        env = directory / ".env"
        env.write_text(
            "MT5_ACCOUNT2_LOGIN=" + login + "\n"
            "MT5_ACCOUNT2_MODE=REAL\n"
            "MT5_ACCOUNT2_CURRENCY=USD\n"
            "MT5_ACCOUNT2_SERVER=VantageMarkets-Live 4\n"
            "MT5_ACCOUNT2_TOKEN=" + token + "\n"
            "MT5_ACCOUNT3_LOGIN=36193223\n"
            "MT5_ACCOUNT3_MODE=REAL\n"
            "MT5_ACCOUNT3_CURRENCY=USD\n"
            "MT5_ACCOUNT3_SERVER=VantageMarkets-Live 4\n"
            "MT5_ACCOUNT3_TOKEN=" + ("dcba" * 16) + "\n",
            encoding="utf-8"
        )
        return env

    def test_private_build_identical_execution_source_and_disarmed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.write_env(root)
            dst = root / "compiler"
            dst.mkdir()
            out = prepare(env, dst, 150, "XAUUSD+")
            self.assertTrue(out.name.endswith("Account2_REAL_USD_Private.mq5"))
            src = out.read_text()
            self.assertIn("COMPILED_ACCOUNT_ID = 2", src)
            self.assertIn("DKACCOUNT2V5", src)
            self.assertIn('input string GoldSymbol = "XAUUSD+";', src)
            self.assertIn("input bool EnableNewEntries = false;", src)
            self.assertIn("input bool EnableLayerEntries = false;", src)
            self.assertIn('input string FeedToken = "' + ("abcd" * 16) + '";', src)
            self.assertNotIn("dcba" * 16, src)
            self.assertNotIn("DKACCOUNT3V5", src)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                prepare(env, dst, 150, "XAUUSD+")

    def test_wrong_broker_server_and_token_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = self.write_env(root, token="short")
            compiler = root / "compiler"
            compiler.mkdir()
            with self.assertRaisesRegex(ValueError, "TOKEN"):
                prepare(env, compiler, 150, "XAUUSD")
            env = self.write_env(root, token="abcd" * 16)
            with self.assertRaisesRegex(ValueError, "Invalid broker gold symbol"):
                prepare(env, compiler, 150, 'XAUUSD";evil')
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                with env.open("a") as fd:
                    fd.write("MT5_ACCOUNT2_LOGIN=1234\n")
                private_identity(env)

    def test_retired_kyle_source_keeps_legacy_rules_while_dan_uses_session_risk(self):
        # Kyle's old private build is retained for audit/rollback, but his
        # live feed is gated off under MT5_DAN_ONLY=true.
        synthetic = "1234" * 16
        kyle = generate(2, 112233, "REAL", "USD", "Broker-Live", 150, feed_token=synthetic)
        dan = generate(3, 112233, "REAL", "USD", "Broker-Live", 150, feed_token=synthetic)
        self.assertIn("COMPILED_ACCOUNT_ID = 2", kyle)
        self.assertIn("COMPILED_ACCOUNT_ID = 3", dan)
        self.assertIn("const double INITIAL_GROUP_RISK_SHARE = 0.85;", kyle)
        self.assertNotIn("UKTradingHour()", kyle)
        self.assertIn("const double INITIAL_GROUP_RISK_SHARE = 1.0;", dan)
        self.assertIn("UKTradingHour()", dan)
        self.assertIn("return hour>=1 && hour<9 ? 10.0 : 4.0;", dan)
        self.assertIn("input bool EnableNewEntries = false;", dan)

if __name__ == "__main__":
    unittest.main()
