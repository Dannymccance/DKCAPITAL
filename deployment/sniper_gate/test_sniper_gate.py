#!/usr/bin/env python3
"""Regression tests for the external Sniper Pro sidecar gate.

Always use a timestamp that has actually occurred. The original test harness
constructed a fake time at :22 of the current minute; before :22, the gate
correctly rejected it as future-dated, producing intermittent false failures.
"""
import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))
import sniper_gate as g


class GateTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.p = Path(self.t.name)
        self.now = int(time.time()) - 5  # Real past timestamp, never future.
        self.bars = self.p / "bars.csv"
        self.deals = self.p / "deals.csv"
        self.out = self.p / "ea.csv"
        lines = ["time;open;high;low;close;volume\n"]
        for minutes_ago in range(90, 0, -1):
            t = self.now // 60 * 60 - minutes_ago * 60
            server = dt.datetime.fromtimestamp(
                t + 7200, dt.timezone.utc
            ).strftime("%Y.%m.%d %H:%M:%S")
            lines.append(f"{server};4000;4004.0;3996.0;4000;200\n")
        self.bars.write_text("".join(lines))
        # An MT5 closing SELL execution (entry OUT) closes a BUY position.
        self.deals.write_text(
            "deal;time;type;entry;reason;symbol\n"
            "1;%d;SELL;OUT;SL;XAUUSD\n"
            "2;%d;SELL;OUT;SL;XAUUSD\n"
            % (self.now - 70 * 60, self.now - 50 * 60)
        )
        self.db = g.db_open(self.p / "db.sqlite3")
        self.a = SimpleNamespace(
            bars=self.bars,
            deals=self.deals,
            output=self.out,
            server_offset_hours=2,
            magic=None,
            mode="live",
            max_age=60,
            max_range=15.0,
            stop_window_minutes=120,
            max_recent_stops=2,
        )

    def tearDown(self):
        self.db.close()
        self.t.cleanup()

    def signal(self, side, ident="1567825992490164238", created=None):
        created = self.now if created is None else created
        return g.parse_signal(
            f"{ident};{side};4000.25;{created};{created + 1800}"
        )

    def status(self, sig):
        row = self.db.execute(
            "SELECT status, reason FROM signal WHERE id=?", (sig["id"],)
        ).fetchone()
        return row

    def test_range(self):
        self.assertEqual(g.m1_range(self.bars, self.now, 2), (8.0, 30))

    def test_sl_inverted_exit(self):
        deals, count = g.sl_history(self.deals, self.now, 2)
        self.assertEqual(count, 2)
        self.assertEqual(len(deals["BUY"]), 2)
        self.assertEqual(len(deals["SELL"]), 0)

    def test_block_buy_allow_sell(self):
        buy = self.signal("BUY")
        g.handle(buy, self.a, self.db)
        self.assertEqual(self.status(buy)[0], "BLOCKED")
        sell = self.signal("SELL", "1567825992490164239")
        g.handle(sell, self.a, self.db)
        self.assertEqual(self.status(sell)[0], "QUEUED")

    def test_volatile_block(self):
        self.bars.write_text(
            self.bars.read_text().replace(
                ";4004.0;3996.0;", ";4010.0;3990.0;"
            )
        )
        sig = self.signal("SELL")
        g.handle(sig, self.a, self.db)
        self.assertEqual(self.status(sig)[0], "BLOCKED")

    def test_dispatch_preserves_line(self):
        sig = self.signal("SELL")
        g.handle(sig, self.a, self.db)
        self.a.handled = self.p / "handled.txt"
        self.a.minimum_hold = 3
        self.assertEqual(self.status(sig)[0], "QUEUED")
        g.dispatch(self.a, self.db)
        self.assertEqual(self.out.read_text().strip(), sig["raw"])

    def test_date_and_time_columns(self):
        s = self.bars.read_text().replace(
            "time;open;high;low;close;volume",
            "date;time;open;high;low;close;volume",
        )
        s = "\n".join(
            line.replace(" ", ";", 1) if i else line
            for i, line in enumerate(s.splitlines())
        ) + "\n"
        self.bars.write_text(s)
        self.assertEqual(g.m1_range(self.bars, self.now, 2)[0], 8.0)

    def test_future_timestamp_rejected(self):
        sig = self.signal("SELL", created=int(time.time()) + 30)
        g.handle(sig, self.a, self.db)
        self.assertEqual(self.status(sig)[0], "BLOCKED")

    def test_missing_deal_history_fails_closed(self):
        self.deals.unlink()
        sig = self.signal("SELL")
        g.handle(sig, self.a, self.db)
        self.assertEqual(self.status(sig)[0], "UNVERIFIED")

    def test_shadow_never_forwards(self):
        self.a.mode = "shadow"
        sig = self.signal("SELL")
        g.handle(sig, self.a, self.db)
        self.assertEqual(self.status(sig)[0], "WOULD_ALLOW")
        g.dispatch(self.a, self.db)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
