"""Safety contracts for read-only fleet audit and disarmed EA binary staging."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FleetReleaseSafetyTests(unittest.TestCase):
    def test_scripts_pass_bash_parser(self):
        for name in ("mt5_v211_fleet_preflight.sh",
                     "stage_mt5_v211_profile.sh"):
            with self.subTest(name=name):
                subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)],
                               check=True, capture_output=True)

    def test_fleet_audit_never_mutates_terminal_or_broker(self):
        s = (ROOT / "scripts/mt5_v211_fleet_preflight.sh").read_text()
        for forbidden in ("systemctl restart", "systemctl start",
                          "systemctl stop", "trade.Buy", "trade.Sell",
                          "docker compose down", "MT5_GATEWAY_TOKEN"):
            self.assertNotIn(forbidden, s)
        for marker in ("dkcapital-mt5-$account.service",
                       "mt5-account{account_id}.json", "(1, 2, 3)",
                       "broker_positions_total", "broker_orders_total",
                       "entry_enabled"):
            self.assertIn(marker, s)

    def test_staging_never_enables_ea_or_overwrites_binary(self):
        s = (ROOT / "scripts/stage_mt5_v211_profile.sh").read_text()
        for forbidden in ("systemctl restart", "systemctl stop",
                          "systemctl start", "trade.Buy", "trade.Sell",
                          "rm -", "EnableNewEntries=true"):
            self.assertNotIn(forbidden, s)
        for marker in ('METAEDITOR_ERRORS', 'METAEDITOR_WARNINGS',
                       'COMPILER=', 'DEST=', '"$BINARY" -nt "$SOURCE"',
                       '-o "$USERNAME" -g "$USERNAME"', 'cmp -s',
                       'HighRiskSignalPct = 2.0;',
                       'COMPILED_ACCOUNT_ID = $ACCOUNT;'):
            self.assertIn(marker, s)


if __name__ == "__main__":
    unittest.main()
