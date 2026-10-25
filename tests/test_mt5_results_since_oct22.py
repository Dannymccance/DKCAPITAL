"""Regression tests: broker-confirmed results recovery is preview-first and idempotent."""
import importlib.util
import json
from datetime import date, datetime, UTC
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
P = ROOT / "ops/backfill/mt5_results_since_oct22.py"
spec = importlib.util.spec_from_file_location("mt5_results_since_oct22", P)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)

class ResultRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.state = {"initialized": True, "cutover": "2026-10-22T12:00:00+00:00",
                      "published": {"old": "123456789", "missed": "pre-cutover"},
                      "active_mt5_groups": [11]}
        self.results = {
            "old": {"closed_at": "2026-10-22T13:00:00+00:00", "currency": "GBP",
                    "pnl": 5.0, "provider": "GWS", "side": "BUY", "source": "GWS - CAPITAL"},
            "missed": {"closed_at": "2026-10-23T13:00:00+00:00", "currency": "GBP",
                       "pnl": -1.0, "provider": "GWS", "side": "SELL", "source": "GWS (VIP)"},
            "new": {"closed_at": "2026-10-25T14:00:00+00:00", "currency": "GBP",
                    "pnl": 2.0, "provider": "GWS", "side": "BUY", "source": "GWS - STORM"},
        }

    def select(self, *, since=date(2026, 10, 22), through=date(2026, 10, 25)):
        with patch.object(backfill, "verified_snapshot", return_value=True), \
             patch.object(backfill, "completed_results", return_value=self.results):
            return backfill.select_results({"version": 2}, [], self.state,
                                           since=since, through=through)

    def test_selects_only_closed_in_period_and_keeps_source(self):
        selected = self.select(through=date(2026, 10, 23))
        self.assertEqual([root for root, _ in selected], ["old", "missed"])
        self.assertEqual(selected[-1][1]["source"], "GWS (VIP)")

    def test_previously_published_or_cutover_entries_skipped(self):
        selected = self.select()
        missing = [root for root, _ in selected if
                   self.state["published"].get(root) in (None, "pre-cutover")]
        self.assertEqual(missing, ["missed", "new"])

    def test_bad_or_stale_broker_data_fails_closed(self):
        with patch.object(backfill, "verified_snapshot", return_value=False):
            with self.assertRaisesRegex(ValueError, "unverified or stale"):
                backfill.select_results({}, [], self.state)

    def test_missing_publication_ledger_refuses_to_send(self):
        self.state["initialized"] = False
        with self.assertRaisesRegex(ValueError, "not initialized"):
            self.select()

    def test_unexpected_publication_state_fails_closed(self):
        self.state["published"]["missed"] = "UNKNOWN"
        with self.assertRaisesRegex(ValueError, "unexpected publication state"):
            self.select()

    def test_marker_recovers_message_and_duplicate_probe(self):
        m = backfill.marker("missed")
        msg = {"id": "123", "embeds": [{"footer": {"text": "backfill | " + m}}]}
        self.assertEqual(backfill.scan_message_marker(msg), m.split("DKC-RESULT-", 1)[1])
        legacy = {"embeds": [{"fields": [
            {"name": "PROVIDER", "value": "GWS"},
            {"name": "SIDE", "value": "SELL"},
            {"name": "TOTAL REALISED P&L", "value": "**£-1.00**"},
        ]}]}
        self.assertTrue(backfill.possible_legacy_duplicate([legacy], self.results["missed"]))

    def test_ledger_update_keeps_existing_fields_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "published.json"
            path.write_text(json.dumps(self.state))
            with patch.object(backfill, "STATE_PATH", path):
                backfill.add_message_id("missed", "9999999999")
                backfill.add_message_id("missed", "9999999999")
                self.assertEqual(json.loads(path.read_text())["active_mt5_groups"], [11])
                self.assertEqual(json.loads(path.read_text())["published"]["missed"], "9999999999")
                with self.assertRaisesRegex(ValueError, "ledger changed"):
                    backfill.add_message_id("missed", "8888888888")

if __name__ == "__main__":
    unittest.main()
