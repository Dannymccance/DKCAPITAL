"""Identity probe is read-only, isolated from three live EAs and their passwords."""
from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]


class TestAccountIdentityProbe(unittest.TestCase):
    def test_mql5_probe_compares_exact_broker_identity(self):
        s=(ROOT/"mt5/DKcapitalAccountIdentityProbe.mq5").read_text()
        for field in (
            "AccountInfoInteger(ACCOUNT_LOGIN)",
            "AccountInfoInteger(ACCOUNT_TRADE_MODE)",
            "AccountInfoInteger(ACCOUNT_MARGIN_MODE)",
            "AccountInfoString(ACCOUNT_CURRENCY)",
            "AccountInfoString(ACCOUNT_SERVER)",
            'input long ExpectedLogin = 24038172;',
            'input string ExpectedServer = "VantageMarkets-Live14";',
            'input string ExpectedCurrency = "GBP";',
            "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING",
            "DK ACCOUNT ID PROBE FAIL",
        ):
            if field == "DK ACCOUNT ID PROBE FAIL":
                self.assertIn('matches ? "PASS" : "FAIL"',s)
            else:
                self.assertIn(field,s)
        for forbidden in ("OrderSend(", "PositionOpen(", "PositionClose(",
                          "trade.Buy(", "trade.Sell(", "WebRequest(",
                          "CTrade trade", "TERMINAL_TRADE_ALLOWED"):
            self.assertNotIn(forbidden,s)
        self.assertIn("void OnStart()",s)
        self.assertNotIn("void OnTick()",s)

    def test_staging_is_kyle_only_no_restart_and_no_overwrite(self):
        s=(ROOT/"scripts/kyle_identity_probe.sh").read_text()
        shell=subprocess.run(["bash","-n",str(ROOT/"scripts/kyle_identity_probe.sh")],
                             capture_output=True,text=True)
        self.assertEqual(shell.returncode,0,shell.stderr)
        self.assertIn("/var/lib/dkcapital-mt5-2/",s)
        self.assertIn('! -e "$TERMINAL/$NAME.ex5"',s)
        self.assertIn("METAEDITOR_ERRORS",s)
        self.assertNotIn("systemctl",s)
        self.assertNotIn("dkcapital-mt5-3",s)


if __name__ == "__main__":
    unittest.main()
