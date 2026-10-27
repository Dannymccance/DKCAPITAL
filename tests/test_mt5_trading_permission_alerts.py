"""All three accounts: explicit MT5 terminal/EA permission health."""
import unittest
from pathlib import Path

from dkcapital.mt5_live_alerts import trading_permission_issues


class PermissionAlerts(unittest.TestCase):
    def test_all_accounts_have_independent_codes(self):
        for prefix in ("MT5_DEMO", "MT5_ACCOUNT2", "MT5_ACCOUNT3"):
            with self.subTest(prefix=prefix):
                self.assertEqual(trading_permission_issues({
                    "terminal_trade_allowed": True,
                    "mql_trade_allowed": True,
                }, prefix), set())
                self.assertEqual(trading_permission_issues({
                    "terminal_trade_allowed": False,
                    "mql_trade_allowed": True,
                }, prefix), {prefix + "_TERMINAL_ALGO_TRADING_DISABLED"})
                self.assertEqual(trading_permission_issues({
                    "terminal_trade_allowed": True,
                    "mql_trade_allowed": False,
                }, prefix), {prefix + "_EA_ALGO_TRADING_DISABLED"})
                self.assertEqual(trading_permission_issues({}, prefix), {
                    prefix + "_TERMINAL_ALGO_TRADING_UNVERIFIED",
                    prefix + "_EA_ALGO_TRADING_UNVERIFIED",
                })

    def test_reporting_and_ea_generator_wiring(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "mt5/DKcapitalV2.mq5").read_text()
        normalize = (root / "src/dkcapital/mt5_account_reporting.py").read_text()
        bot = (root / "src/dkcapital/discord_bot.py").read_text()
        for field in ("terminal_trade_allowed", "mql_trade_allowed"):
            self.assertIn(field, template)
            self.assertIn(field, normalize)
        self.assertIn('trading_permission_issues(snapshot, prefix)', bot)
        self.assertIn('prefix = "MT5_LIVE" if live_only else "MT5_DEMO"', bot)
        self.assertIn("account2_issues", bot)
        self.assertIn("account3_issues", bot)


if __name__ == "__main__":
    unittest.main()
