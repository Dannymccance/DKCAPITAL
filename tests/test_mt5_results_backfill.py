"""Offline tests of the broker-confirmed Oct 12-16 results backfill."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SCRIPT = ROOT / "ops/backfill/mt5_results_oct2026.py"
spec = importlib.util.spec_from_file_location("oct2026_backfill", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


class OctoberBackfillTests(unittest.TestCase):
    def setUp(self):
        self.rows = []
        amounts = [-19.10, 62.67, 70.88, 28.61, -4.59]
        for day, amount in enumerate(amounts, 12):
            self.rows.append((
                f"gws:{day}", {
                    "closed_at": f"2026-10-{day:02d}T12:00:00+00:00",
                    "pnl": amount, "currency": "GBP",
                }
            ))
        self.snapshot = {"version": 2}
        self.state = {
            "initialized": True,
            "cutover": "2026-10-17T20:35:50+00:00",
            "published": {root: "pre-cutover" for root, _ in self.rows},
            "active_mt5_groups": [],
        }

    def select(self):
        with patch.object(backfill, "verified_snapshot", return_value=True), \
             patch.object(backfill, "completed_results",
                          return_value=dict(self.rows)):
            return backfill.select_results(self.snapshot, [], self.state)

    def test_exact_broker_groups_selected(self):
        self.assertEqual(len(self.select()), 5)
        self.assertEqual(round(sum(r["pnl"] for _, r in self.select()), 2), 138.47)

    def test_reconciliation_change_fails_closed(self):
        self.rows.pop()
        with self.assertRaisesRegex(ValueError, "reconciliation changed"):
            self.select()

    def test_unapproved_publication_status_fails_closed(self):
        self.state["published"]["gws:12"] = "unexpected"
        with self.assertRaisesRegex(ValueError, "unexpected publication state"):
            self.select()

    def test_numeric_message_id_supported_on_rerun(self):
        self.state["published"]["gws:12"] = "1561095999139877047"
        self.assertEqual(len(self.select()), 5)

    def test_stale_broker_state_rejected(self):
        with patch.object(backfill, "verified_snapshot", return_value=False):
            with self.assertRaisesRegex(ValueError, "broker snapshot"):
                backfill.select_results(self.snapshot, [], self.state)

    def test_stable_marker_and_history_recovery(self):
        key = backfill.marker("gws:12")
        msg = {"id": "1", "embeds": [{"footer": {"text": "history | " + key}}]}
        self.assertEqual(backfill.scan_message_marker(msg),
                         key.split("DKC-OCT2026-", 1)[1])

    def test_legacy_broker_result_detected_before_duplicate(self):
        result = {"provider": "GWS", "side": "BUY", "pnl": -19.10}
        messages = [{"embeds": [{"fields": [
            {"name": "PROVIDER", "value": "GWS"},
            {"name": "SIDE", "value": "BUY"},
            {"name": "TOTAL REALISED P&L", "value": "**£-19.10**"},
        ]}]}]
        self.assertTrue(backfill.possible_legacy_duplicate(messages, result))
        self.assertFalse(backfill.possible_legacy_duplicate([], result))

    def test_atomic_publication_ledger_preserves_existing_metadata(self):
        import json
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "ledger.json"
            path.write_text(json.dumps(self.state))
            with patch.object(backfill, "STATE_PATH", path):
                backfill.add_message_id("gws:12", "123456789")
            result = json.loads(path.read_text())
            self.assertEqual(result["published"]["gws:12"], "123456789")
            self.assertEqual(result["published"]["gws:13"], "pre-cutover")
            self.assertEqual(result["cutover"], self.state["cutover"])


if __name__ == "__main__":
    unittest.main()
