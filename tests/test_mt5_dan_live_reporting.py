"""Dan Live Account reporting uses only independently verified Account 3 data."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from dkcapital.mt5_active_account import account_label, live_identity, matches_active_account
from dkcapital.mt5_account_reporting import normalize_telemetry, summarize
from dkcapital.mt5_entry_confirmations import build_confirmation
from dkcapital.mt5_pip_history import record_weekly_average
from dkcapital.mt5_signal_cards import broker_signal_state
from dkcapital.mt5_trade_results import verified_snapshot


class LiveReportingTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "MT5_DAN_ONLY": "true",
            "MT5_ACCOUNT3_LOGIN": "710003",
            "MT5_ACCOUNT3_MODE": "REAL",
            "MT5_ACCOUNT3_CURRENCY": "USD",
            "MT5_ACCOUNT3_SERVER": "Vantage-Live",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def broker(self):
        current = datetime.now(UTC).isoformat()
        return normalize_telemetry({
            "version": 2, "mode": "REAL", "currency": "USD",
            "login": 710003, "server": "Vantage-Live",
            "generated_at": current,
            "balance": 2500, "equity": 3100, "credit": 625,
            "margin": 0, "free_margin": 3100, "bid": 4000, "ask": 4000.5,
            "positions": [], "deals": [], "feed_http": 200,
            "entry_enabled": False, "signal_risk_cap_pct": 10.0,
            "signal_risk_cap_account_currency": 200,
            "portfolio_risk_cap_account_currency": 200,
        }, 710003, expected_mode="REAL", expected_currency="USD",
           expected_server="Vantage-Live")

    def test_live_mode_rejects_demo_kyle_wrong_server_and_stale(self):
        snap = self.broker()
        self.assertEqual(account_label(), "Live Account")
        self.assertEqual(live_identity()["server"], "Vantage-Live")
        self.assertTrue(matches_active_account(snap))
        self.assertTrue(verified_snapshot(snap))
        self.assertTrue(broker_signal_state("-100:112", snap)["fresh"])
        for modification in ({"login": 5057220041, "mode": "DEMO", "currency": "GBP"},
                             {"login": 710002}, {"server": "Another-Server"}):
            with self.subTest(modification=modification):
                wrong = {**snap, **modification}
                self.assertFalse(verified_snapshot(wrong))
                self.assertFalse(broker_signal_state("-100:112", wrong)["fresh"])
        old = {**snap, "received_at": "2026-09-01T00:00:00+00:00"}
        self.assertFalse(verified_snapshot(old))

    def test_live_dashboard_summary_uses_usd_risk_not_demo_gbp_key(self):
        snap = self.broker()
        snap["positions"] = [{
            "ticket": 100, "magic": 5100000001, "group": 510000000,
            "leg": 1, "side": "BUY", "lots": 0.1, "entry": 4000,
            "sl": 3990, "tp": 4010, "profit": 10, "risk_gbp": None,
            "risk_account_currency": 30.0,
        }]
        result = summarize(snap)
        self.assertFalse(result["risk_unknown"])
        self.assertEqual(result["portfolio_risk_gbp"], 30.0)
        self.assertEqual(snap["portfolio_risk_cap_account_currency"], 200)
        self.assertEqual(snap["credit"], 625)
        self.assertEqual(result["floating_gbp"], -25.0)  # 3100 equity - 2500 balance - 625 bonus

    def test_new_live_pip_history_does_not_absorb_demo_archives(self):
        snap = self.broker()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            live_path = root / "mt5-account3-live-pip-history.json"
            self.assertIsNone(record_weekly_average(live_path, snap))
            saved = json.loads(live_path.read_text())
            self.assertEqual((saved["login"], saved["mode"], saved["currency"], saved["server"]),
                             (710003, "REAL", "USD", "Vantage-Live"))
            demo_path = root / "legacy-demo-history.json"
            demo_path.write_text(json.dumps({
                "version": saved["version"], "login": 5057220041,
                "currency": "GBP", "deals": [],
            }))
            with self.assertRaisesRegex(ValueError, "identity"):
                record_weekly_average(demo_path, snap)
            snap["server"] = "different"
            with self.assertRaisesRegex(ValueError, "identity"):
                record_weekly_average(live_path, snap)

    def test_entry_confirmation_is_only_live_account(self):
        now = datetime.now(UTC)
        snap = self.broker()
        signal = {"signal_id": "-100:112", "direction": "BUY",
                  "source_style": "gws", "opened_at": now.isoformat(),
                  "entry_low": 4000, "entry_high": 4001}
        content, proofs = build_confirmation(signal, {3: snap}, {}, now)
        self.assertIn("**Live Account:**", content)
        self.assertNotIn("**Demo:**", content)
        self.assertNotIn("**Kyle:**", content)
        self.assertNotIn("**Dan:**", content)
        self.assertEqual(proofs, {})

if __name__ == "__main__":
    unittest.main()
