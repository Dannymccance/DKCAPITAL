from __future__ import annotations

import unittest

from dkcapital.discord_bot import (
    _history_summary,
    _local_timestamp,
    _paper_dashboard_embed,
    _paper_pips,
    _paper_realised_pips,
    _signal_embed,
    _signal_pnl_text,
)


class DiscordSignalDisplayTests(unittest.TestCase):
    def test_xau_pips_use_configured_point_size(self) -> None:
        self.assertEqual(_paper_pips("BUY", 4157.0, 4158.0, 0.01), 100.0)
        self.assertEqual(_paper_pips("SELL", 4157.0, 4156.0, 0.01), 100.0)

    def test_weighted_realised_pips_for_partial_exits(self) -> None:
        position = {
            "direction": "SELL",
            "entry_price": 4157.0,
            "initial_quantity_oz": 100.0,
            "history": [
                {
                    "kind": "close",
                    "price": 4150.0,
                    "quantity_oz": 50.0,
                },
                {
                    "kind": "close",
                    "price": 4147.0,
                    "quantity_oz": 25.0,
                },
            ],
        }
        self.assertEqual(_paper_realised_pips(position, 0.01), 600.0)

    def test_paper_dashboard_contains_account_drawdown_and_trade_pnl(self) -> None:
        state = {
            "strategy_mode": "test_strategy",
            "entry_policy": "all_parsed",
            "starting_balance_usd": 100000.0,
            "balance_usd": 100350.0,
            "equity_usd": 100950.0,
            "realized_pnl_usd": 350.0,
            "unrealized_pnl_usd": 600.0,
            "peak_equity_usd": 101000.0,
            "current_drawdown_usd": 50.0,
            "current_drawdown_pct": 0.0495,
            "max_drawdown_usd": 500.0,
            "max_drawdown_pct": 0.5,
            "last_mark_price": 4145.0,
            "last_mark_at": "2026-09-29T12:00:00+00:00",
            "updated_at": "2026-09-29T12:00:00+00:00",
            "candidates": [
                {
                    "signal_id": "elite:1",
                    "source_style": "elite",
                    "execution_status": "OPEN",
                }
            ],
            "positions": [
                {
                    "position_id": "paper:elite:1",
                    "signal_id": "elite:1",
                    "symbol": "XAUUSD",
                    "direction": "SELL",
                    "entry_price": 4157.0,
                    "initial_quantity_oz": 100.0,
                    "remaining_quantity_oz": 50.0,
                    "stop_loss": 4157.0,
                    "status": "OPEN",
                    "last_mark_price": 4145.0,
                    "unrealized_pnl_usd": 600.0,
                    "realized_pnl_usd": 350.0,
                    "history": [
                        {
                            "kind": "close",
                            "price": 4150.0,
                            "quantity_oz": 50.0,
                            "realized_pnl_usd": 350.0,
                        }
                    ],
                }
            ],
        }
        embed = _paper_dashboard_embed(
            state,
            timezone_name="Europe/Isle_of_Man",
            pip_size=0.01,
        )
        rendered = "\n".join(
            str(field.value) for field in embed.fields
        )
        self.assertIn("Live equity", rendered)
        self.assertIn("Maximum", rendered)
        self.assertIn("+1,200.0 pips", rendered)
        self.assertIn("+350.0 pips", rendered)
        self.assertIn("$+600.00", rendered)

    def test_signal_card_shows_actual_paper_usd_pips_and_r(self) -> None:
        signal = {
            "signal_id": "gws:1",
            "direction": "BUY",
            "symbol": "XAUUSD",
            "source_style": "gws",
            "entry_low": 4147.0,
            "entry_high": 4150.0,
            "original_sl": 4342.0,
            "current_sl": 4148.5,
            "sl_mode": "BREAKEVEN",
            "tps": {"1": 4155.0, "2": 4157.5, "3": 4203.0},
            "tp_hits": [1, 2],
            "remaining_fraction": 0.7,
            "partial_close_count": 1,
            "status": "ACTIVE",
            "history": [],
            "opened_at": "2026-09-29T15:28:32+00:00",
            "root_message_id": 6786,
        }
        position = {
            "signal_id": "gws:1",
            "direction": "BUY",
            "entry_price": 4150.0,
            "initial_quantity_oz": 100.0,
            "remaining_quantity_oz": 70.0,
            "lot_size": 1.0,
            "stop_loss": 4150.0,
            "stop_source": "BREAKEVEN",
            "temporary_stop_active": False,
            "last_mark_price": 4155.0,
            "realized_pnl_usd": 90.0,
            "unrealized_pnl_usd": 350.0,
            "initial_risk_usd": 500.0,
            "status": "OPEN",
            "history": [
                {
                    "kind": "close",
                    "price": 4153.0,
                    "quantity_oz": 30.0,
                    "realized_pnl_usd": 90.0,
                }
            ],
        }

        embed = _signal_embed(
            signal,
            spot_quote={
                "price": 4155.0,
                "computed_at": "2026-09-29T15:35:00+00:00",
            },
            paper_position=position,
            pip_size=0.01,
            timezone_name="Europe/Isle_of_Man",
        )
        fields = {field.name: str(field.value) for field in embed.fields}

        self.assertIn("Fill **4150**", fields["Paper Execution"])
        self.assertIn("BE 4150", fields["Paper Execution"])
        self.assertIn("$+90.00", fields["Realised PnL"])
        self.assertIn("+90.0 pips", fields["Realised PnL"])
        self.assertIn("+0.18R", fields["Realised PnL"])
        self.assertIn("$+350.00", fields["Open PnL"])
        self.assertIn("+500.0 pips", fields["Open PnL"])
        self.assertIn("$+440.00", fields["Total PnL"])
        self.assertIn("+440.0 weighted pips", fields["Total PnL"])
        self.assertIn("+0.88R", fields["Total PnL"])

    def test_signal_card_without_paper_position_still_shows_live_pip_move(self) -> None:
        signal = {
            "signal_id": "gws:6786",
            "direction": "BUY",
            "symbol": "XAUUSD",
            "source_style": "gws",
            "entry_low": 4147.0,
            "entry_high": 4150.0,
            "original_sl": 4342.0,
            "current_sl": 4148.5,
            "sl_mode": "BREAKEVEN",
            "tps": {"1": 4155.0, "2": 4157.5, "3": 4203.0},
            "tp_hits": [1, 2],
            "remaining_fraction": 1.0,
            "partial_close_count": 0,
            "status": "ACTIVE",
            "history": [],
            "opened_at": "2026-09-29T15:28:32+00:00",
            "root_message_id": 6786,
        }

        embed = _signal_embed(
            signal,
            spot_quote={
                "price": 4158.73,
                "computed_at": "2026-09-29T15:55:00+00:00",
            },
            paper_position=None,
            pip_size=0.01,
            timezone_name="Europe/Isle_of_Man",
        )
        fields = {field.name: str(field.value) for field in embed.fields}
        self.assertIn("+1,023.0 pips", fields["Open PnL"])

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
