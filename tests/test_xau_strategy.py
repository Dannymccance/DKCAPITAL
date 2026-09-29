from __future__ import annotations

import unittest

from dkcapital.paper_trading import PaperAccount
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig


def sig(
    signal_id: str,
    *,
    direction: str = "BUY",
    sl: float | None = 4150.0,
    tps: dict[str, float] | None = None,
    opened_at: str = "2026-09-29T13:30:00+00:00",
) -> dict:
    return {
        "signal_id": signal_id,
        "symbol": "XAUUSD",
        "direction": direction,
        "source_style": "elite",
        "chat_id": 123,
        "root_message_id": 1,
        "opened_at": opened_at,
        "status": "ACTIVE",
        "entry_low": 4159.0,
        "entry_high": 4162.0,
        "original_sl": sl,
        "current_sl": sl,
        "tps": tps or {},
        "remaining_fraction": 1.0,
        "partial_close_count": 0,
        "source_message_ids": [1],
        "history": [
            {
                "timestamp": opened_at,
                "message_id": 1,
                "kind": "opened",
                "entry_low": 4159.0,
                "entry_high": 4162.0,
                "sl": sl,
            }
        ],
    }


def quote(price: float, *, spread: float = 0.2) -> dict:
    return {
        "symbol": "XAUUSD",
        "price": price,
        "bid": price - spread / 2.0,
        "ask": price + spread / 2.0,
        "computed_at": "2026-09-29T13:31:00+00:00",
        "source": "test",
    }


class XauStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = XauSignalFollowingStrategy(
            XauStrategyConfig(
                risk_pct=0.005,
                direction_risk_cap_pct=0.008,
                min_trade_risk_pct=0.001,
                daily_loss_pct=0.02,
                contract_oz_per_lot=100.0,
                lot_step=0.01,
                timezone_name="Europe/Isle_of_Man",
            )
        )

    def account(self) -> PaperAccount:
        account = PaperAccount(strategy_mode="signal_follow_v1")
        account.activate_strategy(
            "signal_follow_v1",
            activated_at="2026-09-29T13:00:00+00:00",
        )
        return account

    def test_standard_trade_risks_half_percent_of_balance(self) -> None:
        account = self.account()
        account.sync_signal(sig("buy:1", sl=4150.0))
        self.strategy.process(account, quote(4160.0))

        position = account.positions["paper:buy:1"]
        # BUY fills at ask 4160.10. Risk distance 10.10. A $500 budget
        # floors to 0.49 lots / 49 oz, staying below the risk ceiling.
        self.assertAlmostEqual(position.lot_size, 0.49)
        self.assertAlmostEqual(position.initial_quantity_oz, 49.0)
        self.assertLessEqual(position.initial_risk_usd, 500.0)
        self.assertGreater(position.initial_risk_usd, 490.0)

    def test_second_same_direction_trade_is_reduced_to_remaining_book_capacity(self) -> None:
        account = self.account()
        first = sig("buy:1", sl=4150.0)
        second = sig(
            "buy:2",
            sl=4150.0,
            opened_at="2026-09-29T13:32:00+00:00",
        )
        account.sync_signal(first)
        self.strategy.process(account, quote(4160.0))

        account.sync_signal(second)
        self.strategy.process(account, quote(4160.0))

        p1 = account.positions["paper:buy:1"]
        p2 = account.positions["paper:buy:2"]
        total_risk = account.current_directional_risk_usd("BUY")
        self.assertLessEqual(total_risk, account.balance_usd * 0.008 + 1e-6)
        self.assertLess(p2.initial_risk_usd, p1.initial_risk_usd)
        self.assertGreaterEqual(p2.initial_risk_usd, account.balance_usd * 0.001)

    def test_opposite_direction_can_hedge_with_independent_risk_cap(self) -> None:
        account = self.account()
        account.sync_signal(sig("buy:1", direction="BUY", sl=4150.0))
        self.strategy.process(account, quote(4160.0))

        account.sync_signal(
            sig(
                "sell:1",
                direction="SELL",
                sl=4170.0,
                opened_at="2026-09-29T13:32:00+00:00",
            )
        )
        self.strategy.process(account, quote(4160.0))

        self.assertIn("paper:buy:1", account.positions)
        self.assertIn("paper:sell:1", account.positions)
        self.assertGreater(account.current_directional_risk_usd("BUY"), 0.0)
        self.assertGreater(account.current_directional_risk_usd("SELL"), 0.0)

    def test_missing_sl_enters_with_temporary_protective_stop(self) -> None:
        account = self.account()
        account.sync_signal(sig("buy:1", sl=None))
        self.strategy.process(account, quote(4160.0))

        position = account.positions["paper:buy:1"]
        self.assertTrue(position.temporary_stop_active)
        self.assertEqual(position.stop_source, "TEMPORARY")
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4153.0)
        self.assertLessEqual(position.initial_risk_usd, 500.0)
        self.assertEqual(
            account.candidates["buy:1"].execution_status,
            "OPEN",
        )

    def test_valid_structural_stop_already_crossed_is_rejected(self) -> None:
        account = self.account()
        account.sync_signal(sig("buy:1", sl=4150.0))
        self.strategy.process(account, quote(4145.0))
        self.assertNotIn("paper:buy:1", account.positions)
        self.assertEqual(
            account.candidates["buy:1"].execution_status,
            "REJECTED_INVALIDATED",
        )

    def test_wrong_side_provider_sl_enters_with_temp_then_corrects(self) -> None:
        account = self.account()
        malformed = sig(
            "gws:6786",
            sl=4342.0,
            tps={"1": 4155.0, "2": 4157.5, "3": 4203.0},
            opened_at="2026-09-29T15:28:32+00:00",
        )
        malformed["entry_low"] = 4148.0
        malformed["entry_high"] = 4151.0
        account.sync_signal(malformed)
        self.strategy.process(account, quote(4158.73))

        candidate = account.candidates["gws:6786"]
        position = account.positions["paper:gws:6786"]
        initial_qty = position.remaining_quantity_oz

        self.assertEqual(candidate.execution_status, "OPEN")
        self.assertTrue(position.temporary_stop_active)
        self.assertEqual(position.stop_source, "TEMPORARY")
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4142.0)
        self.assertAlmostEqual(float(position.last_provider_stop_seen or 0.0), 4342.0)

        corrected = sig(
            "gws:6786",
            sl=4142.0,
            tps={"1": 4155.0, "2": 4157.5, "3": 4203.0},
            opened_at="2026-09-29T15:28:32+00:00",
        )
        corrected["entry_low"] = 4147.0
        corrected["entry_high"] = 4150.0
        corrected["history"][0]["entry_low"] = 4147.0
        corrected["history"][0]["entry_high"] = 4150.0
        corrected["history"][0]["sl"] = 4142.0

        account.sync_signal(corrected)
        self.strategy.process(account, quote(4158.73))

        position = account.positions["paper:gws:6786"]
        self.assertFalse(position.temporary_stop_active)
        self.assertEqual(position.stop_source, "PROVIDER")
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4142.0)
        # A closer provider stop never increases the position.
        self.assertLessEqual(position.remaining_quantity_oz, initial_qty)

    def test_entry_zone_edit_recalculates_temp_stop_and_trims_risk(self) -> None:
        account = self.account()
        malformed = sig(
            "gws:6786",
            sl=4342.0,
            tps={"3": 4203.0},
            opened_at="2026-09-29T15:28:32+00:00",
        )
        malformed["entry_low"] = 4148.0
        malformed["entry_high"] = 4151.0
        account.sync_signal(malformed)
        self.strategy.process(account, quote(4158.73))

        position = account.positions["paper:gws:6786"]
        before_qty = position.remaining_quantity_oz
        before_risk_cap = position.initial_risk_usd
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4142.0)

        edited = dict(malformed)
        edited["entry_low"] = 4147.0
        edited["entry_high"] = 4150.0
        edited["history"] = [
            {
                "timestamp": "2026-09-29T15:28:32+00:00",
                "message_id": 1,
                "kind": "opened",
                "entry_low": 4147.0,
                "entry_high": 4150.0,
                "sl": 4342.0,
            }
        ]
        account.sync_signal(edited)
        self.strategy.process(account, quote(4158.73))

        position = account.positions["paper:gws:6786"]
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4141.0)
        self.assertLess(position.remaining_quantity_oz, before_qty)
        current_risk = (
            position.entry_price - float(position.stop_loss or 0.0)
        ) * position.remaining_quantity_oz
        self.assertLessEqual(current_risk, before_risk_cap + 1e-6)

    def test_farther_corrected_sl_reduces_size_but_never_exceeds_risk_cap(self) -> None:
        account = self.account()
        malformed = sig("buy:1", sl=None, tps={"3": 4200.0})
        account.sync_signal(malformed)
        self.strategy.process(account, quote(4160.0))

        position = account.positions["paper:buy:1"]
        original_qty = position.remaining_quantity_oz
        original_risk = position.initial_risk_usd
        self.assertTrue(position.temporary_stop_active)

        corrected = sig("buy:1", sl=4145.0, tps={"3": 4200.0})
        account.sync_signal(corrected)
        self.strategy.process(account, quote(4160.0))

        position = account.positions["paper:buy:1"]
        self.assertFalse(position.temporary_stop_active)
        self.assertEqual(position.stop_source, "PROVIDER")
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4145.0)
        self.assertLess(position.remaining_quantity_oz, original_qty)
        current_risk = (
            position.entry_price - float(position.stop_loss or 0.0)
        ) * position.remaining_quantity_oz
        self.assertLessEqual(current_risk, original_risk + 1e-6)
        self.assertLessEqual(
            account.current_directional_risk_usd("BUY"),
            account.balance_usd * 0.008 + 1e-6,
        )

    def test_legacy_wrong_side_rejection_now_enters_with_temp_stop(self) -> None:
        account = self.account()
        malformed = sig("gws:6786", sl=4342.0, tps={"3": 4203.0})
        malformed["entry_low"] = 4148.0
        malformed["entry_high"] = 4151.0
        account.sync_signal(malformed)
        candidate = account.candidates["gws:6786"]
        candidate.execution_status = "REJECTED_INVALIDATED"
        candidate.execution_note = "Old engine rejection"

        self.strategy.process(account, quote(4158.73))
        position = account.positions["paper:gws:6786"]
        self.assertEqual(candidate.execution_status, "OPEN")
        self.assertTrue(position.temporary_stop_active)
        self.assertAlmostEqual(float(position.stop_loss or 0.0), 4142.0)

    def test_all_targets_already_passed_are_not_chased(self) -> None:
        account = self.account()
        account.sync_signal(
            sig(
                "buy:1",
                sl=4150.0,
                tps={"1": 4166.0, "2": 4168.5, "3": 4203.0},
            )
        )
        self.strategy.process(account, quote(4210.0))
        self.assertNotIn("paper:buy:1", account.positions)
        self.assertEqual(
            account.candidates["buy:1"].execution_status,
            "REJECTED_TARGETS_ALREADY_PASSED",
        )

    def test_tp1_closes_30_percent_and_moves_remaining_stop_to_be(self) -> None:
        account = self.account()
        account.sync_signal(
            sig(
                "buy:1",
                sl=4150.0,
                tps={"1": 4170.0, "2": 4180.0, "3": 4200.0},
            )
        )
        self.strategy.process(account, quote(4160.0))
        position = account.positions["paper:buy:1"]
        initial_qty = position.initial_quantity_oz
        entry = position.entry_price

        self.strategy.process(account, quote(4170.2))
        self.assertAlmostEqual(position.remaining_quantity_oz, initial_qty * 0.70)
        self.assertTrue(position.breakeven_locked)
        self.assertAlmostEqual(float(position.stop_loss or 0.0), entry)
        self.assertEqual(position.tp_hits, [1])

    def test_provider_sl_cannot_worsen_after_breakeven_lock(self) -> None:
        account = self.account()
        signal = sig(
            "buy:1",
            sl=4150.0,
            tps={"1": 4170.0, "2": 4180.0, "3": 4200.0},
        )
        account.sync_signal(signal)
        self.strategy.process(account, quote(4160.0))
        self.strategy.process(account, quote(4170.2))
        position = account.positions["paper:buy:1"]
        entry = position.entry_price

        updated = sig(
            "buy:1",
            sl=4145.0,
            tps={"1": 4170.0, "2": 4180.0, "3": 4200.0},
        )
        updated["history"] = signal["history"] + [
            {
                "timestamp": "2026-09-29T13:35:00+00:00",
                "message_id": 2,
                "kind": "trade_update",
                "sl_before": 4150.0,
                "sl_after": 4145.0,
                "market": quote(4172.0),
            }
        ]
        account.sync_signal(updated)
        self.strategy.process(account, quote(4172.0))
        self.assertAlmostEqual(float(position.stop_loss or 0.0), entry)

    def test_daily_equity_floor_blocks_new_entries_but_does_not_close_existing(self) -> None:
        account = self.account()
        account.ensure_trading_day("2026-09-29", daily_loss_pct=0.02)
        account.sync_signal(sig("buy:1", sl=4150.0))
        self.strategy.process(account, quote(4160.0))
        first = account.positions["paper:buy:1"]

        # Force account equity through the 2% daily floor without touching the
        # first position's structural stop by adding an independent paper loss.
        account.realized_pnl_usd = -2100.0
        account.balance_usd = 97900.0
        account._revalue()
        account.check_daily_stop(timestamp="2026-09-29T14:00:00+00:00")
        self.assertTrue(account.daily_stop_triggered)
        self.assertEqual(first.status, "OPEN")

        account.sync_signal(
            sig(
                "buy:2",
                sl=4150.0,
                opened_at="2026-09-29T14:01:00+00:00",
            )
        )
        self.strategy.process(account, quote(4160.0))
        self.assertNotIn("paper:buy:2", account.positions)
        self.assertEqual(
            account.candidates["buy:2"].execution_status,
            "SKIPPED_DAILY_STOP",
        )

    def test_pre_strategy_signal_is_recorded_but_not_opened(self) -> None:
        account = self.account()
        account.sync_signal(
            sig(
                "old:1",
                sl=4150.0,
                opened_at="2026-09-29T12:00:00+00:00",
            ),
            initial_snapshot=True,
        )
        self.strategy.process(account, quote(4160.0))
        self.assertNotIn("paper:old:1", account.positions)
        self.assertEqual(
            account.candidates["old:1"].execution_status,
            "SKIPPED_PRE_STRATEGY",
        )


if __name__ == "__main__":
    unittest.main()
