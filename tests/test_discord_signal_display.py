from __future__ import annotations

import unittest

from dkcapital.discord_bot import (
    _history_summary,
    _local_timestamp,
    _signal_pnl_text,
)


class DiscordSignalDisplayTests(unittest.TestCase):
    def test_isle_of_man_footer_uses_bst_in_september(self) -> None:
        rendered = _local_timestamp(
            "2026-09-29T11:06:29+00:00",
            "Europe/Isle_of_Man",
        )
        self.assertEqual(rendered, "29 Sep 2026 12:06:29 BST")

    def test_partial_history_calculates_realised_and_open_r(self) -> None:
        signal = {
            "direction": "SELL",
            "symbol": "XAUUSD",
            "entry_low": 4157.0,
            "entry_high": 4157.0,
            "original_sl": 4164.0,
            "current_sl": 4157.0,
            "remaining_fraction": 0.25,
            "status": "ACTIVE",
            "history": [
                {
                    "timestamp": "2026-09-29T11:06:29+00:00",
                    "message_id": 6256,
                    "kind": "opened",
                    "entry_low": 4157.0,
                    "entry_high": 4157.0,
                    "sl": 4164.0,
                },
                {
                    "timestamp": "2026-09-29T11:42:20+00:00",
                    "message_id": 6257,
                    "kind": "partial_close",
                    "partial_percent": 50.0,
                    "remaining_fraction": 0.5,
                    "market": {"price": 4150.0},
                },
                {
                    "timestamp": "2026-09-29T11:42:20+00:00",
                    "message_id": 6257,
                    "kind": "trade_update",
                    "sl_before": 4164.0,
                    "sl_after": 4157.0,
                    "market": {"price": 4150.0},
                },
                {
                    "timestamp": "2026-09-29T11:45:29+00:00",
                    "message_id": 6259,
                    "kind": "partial_close",
                    "partial_percent": 50.0,
                    "remaining_fraction": 0.25,
                    "market": {"price": 4147.0},
                },
            ],
        }

        lines, _, realised_r, missing = _history_summary(
            signal,
            "Europe/Isle_of_Man",
        )
        self.assertFalse(missing)
        self.assertAlmostEqual(float(realised_r or 0.0), 0.8571428571)
        self.assertTrue(any("Realised +0.50R" in line for line in lines))
        self.assertTrue(any("Realised +0.36R" in line for line in lines))
        self.assertTrue(any("SL -> 4157" in line for line in lines))

        realised, floating, total = _signal_pnl_text(
            signal,
            {"price": 4143.0},
            "Europe/Isle_of_Man",
        )
        self.assertEqual(realised, "+0.86R")
        self.assertEqual(floating, "+0.50R")
        self.assertEqual(total, "+1.36R")

    def test_legacy_partial_without_market_price_is_flagged(self) -> None:
        signal = {
            "direction": "SELL",
            "symbol": "XAUUSD",
            "entry_low": 4157.0,
            "entry_high": 4157.0,
            "original_sl": 4164.0,
            "remaining_fraction": 0.5,
            "status": "ACTIVE",
            "history": [
                {
                    "timestamp": "2026-09-29T11:06:29+00:00",
                    "message_id": 6256,
                    "kind": "opened",
                    "sl": 4164.0,
                },
                {
                    "timestamp": "2026-09-29T11:42:20+00:00",
                    "message_id": 6257,
                    "kind": "partial_close",
                    "partial_percent": 50.0,
                },
            ],
        }
        realised, floating, total = _signal_pnl_text(
            signal,
            {"price": 4150.0},
            "Europe/Isle_of_Man",
        )
        self.assertIn("unpriced legacy exits", realised)
        self.assertEqual(floating, "+0.50R")
        self.assertIn("unpriced legacy exits", total)


if __name__ == "__main__":
    unittest.main()
