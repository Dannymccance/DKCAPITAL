from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from dkcapital.mt5_pip_history import record_weekly_average, weekly_average


LOGIN = 5057220041
MAGIC_A = 5100000001
MAGIC_B = 5100000011


def deal(ticket, position, group, entry_type, side, lots, price, at):
    return {
        "ticket": ticket, "position_id": position, "group": group,
        "magic": MAGIC_A if group == 1 else MAGIC_B,
        "entry_type": entry_type, "side": side, "lots": lots,
        "price": price, "profit": 0.0, "swap": 0.0,
        "commission": 0.0, "time": at, "reason": "EXPERT",
    }


def snapshot(deals, *, login=LOGIN):
    return {
        "version": 2, "login": login, "mode": "DEMO",
        "currency": "GBP", "deals": deals,
    }


class LifetimeWeeklyPipTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 26, 15, tzinfo=UTC)
        self.entries = [
            deal(1, 11, 1, "IN", "BUY", .02, 4000, "2026-10-12T08:00:00+00:00"),
            deal(2, 12, 1, "IN", "BUY", .04, 4000, "2026-10-12T08:00:01+00:00"),
            deal(3, 11, 1, "OUT", "SELL", .02, 4003, "2026-10-13T10:00:00+00:00"),
            deal(4, 12, 1, "OUT", "SELL", .04, 4001, "2026-10-14T10:00:00+00:00"),
            deal(5, 21, 2, "IN", "SELL", .03, 4100, "2026-10-20T11:00:00+00:00"),
            deal(6, 21, 2, "OUT", "BUY", .03, 4098, "2026-10-21T11:00:00+00:00"),
        ]

    def test_averages_all_elapsed_weeks_including_current_zero_week(self):
        result = weekly_average(self.entries, now=self.now)
        self.assertEqual(result["first_trade"], "2026-10-12")
        self.assertEqual(result["weeks"], 3)
        self.assertEqual(result["total_pips"], 366.7)
        self.assertEqual(result["average_pips"], 122.2)
        self.assertEqual(result["closed_signals"], 2)

    def test_negative_signals_and_no_closing_deals(self):
        entries = [
            deal(10, 90, 3, "IN", "BUY", .01, 4000, "2026-10-12T10:00:00Z"),
        ]
        self.assertEqual(weekly_average(entries, now=self.now)["average_pips"], 0.0)
        entries.append(deal(11, 90, 3, "OUT", "SELL", .01, 3994,
                            "2026-10-12T11:00:00Z"))
        self.assertEqual(weekly_average(entries, now=self.now)["average_pips"], -200.0)

    def test_archives_overlapping_snapshots_without_double_counting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pips.json"
            first = record_weekly_average(path, snapshot(self.entries[:4]), now=self.now)
            self.assertEqual(first["average_pips"], 55.6)
            later = record_weekly_average(path, snapshot(self.entries[2:]), now=self.now)
            self.assertEqual(later["average_pips"], 122.2)
            self.assertEqual(later["archived_deals"], 6)
            same = record_weekly_average(path, snapshot(self.entries[2:]), now=self.now)
            self.assertEqual(same, later)
            archived = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(archived["deals"]), 6)

    def test_conflicting_ticket_and_other_login_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pips.json"
            record_weekly_average(path, snapshot(self.entries), now=self.now)
            corrupted = dict(self.entries[0], price=3999)
            with self.assertRaisesRegex(ValueError, "changed"):
                record_weekly_average(path, snapshot([corrupted]), now=self.now)
            with self.assertRaisesRegex(ValueError, "identity"):
                record_weekly_average(path, snapshot(self.entries, login=123), now=self.now)
            self.assertEqual(len(json.loads(path.read_text())["deals"]), 6)

    def test_marks_bounded_ea_history_as_potentially_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [self.entries[0]] * 1000
            # The cap indicator reflects the inbound deal count, not archival total.
            result = record_weekly_average(Path(tmp) / "pips.json",
                                           snapshot(records), now=self.now)
            self.assertTrue(result["source_capped"])
            self.assertEqual(result["archived_deals"], 1)

    def test_no_data_or_naive_current_time(self):
        self.assertIsNone(weekly_average([], now=self.now))
        with self.assertRaisesRegex(ValueError, "timezone"):
            weekly_average(self.entries, now=datetime(2026, 10, 26))


if __name__ == "__main__":
    unittest.main()
