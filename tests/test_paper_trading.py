from __future__ import annotations

import unittest

from dkcapital.paper_trading import PaperAccount


def signal(
    signal_id: str = "elite:1",
    *,
    symbol: str = "XAUUSD",
    direction: str = "SELL",
) -> dict:
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "source_style": "elite",
        "chat_id": 123,
        "root_message_id": 6256,
        "opened_at": "2026-09-29T11:06:29+00:00",
        "status": "ACTIVE",
        "entry_low": 4157.0,
        "entry_high": 4157.0,
        "original_sl": 4164.0,
        "current_sl": 4164.0,
        "tps": {},
        "remaining_fraction": 1.0,
        "partial_close_count": 0,
        "source_message_ids": [6256],
        "history": [],
    }


class PaperAccountTests(unittest.TestCase):
    def test_starts_with_100k_and_observe_only(self) -> None:
        account = PaperAccount()
        snapshot = account.snapshot()
        self.assertEqual(snapshot["starting_balance_usd"], 100000.0)
        self.assertEqual(snapshot["balance_usd"], 100000.0)
        self.assertEqual(snapshot["equity_usd"], 100000.0)
        self.assertEqual(snapshot["strategy_mode"], "observe_only")
        self.assertEqual(snapshot["open_position_count"], 0)

    def test_only_xauusd_signals_are_ingested(self) -> None:
        account = PaperAccount()
        changed, result = account.sync_signal(signal())
        self.assertTrue(changed)
        self.assertEqual(result, "created")
        self.assertIn("elite:1", account.candidates)

        changed, result = account.sync_signal(
            signal("btc:1", symbol="BTCUSDT", direction="BUY")
        )
        self.assertFalse(changed)
        self.assertEqual(result, "symbol_filtered")
        self.assertNotIn("btc:1", account.candidates)

    def test_duplicate_signal_snapshot_is_idempotent(self) -> None:
        account = PaperAccount()
        account.sync_signal(signal())
        changed, result = account.sync_signal(signal())
        self.assertFalse(changed)
        self.assertEqual(result, "unchanged")
        self.assertEqual(len(account.candidates), 1)

    def test_signal_updates_modify_candidate_not_duplicate_it(self) -> None:
        account = PaperAccount()
        first = signal()
        account.sync_signal(first)

        updated = signal()
        updated["current_sl"] = 4157.0
        updated["partial_close_count"] = 1
        updated["remaining_fraction"] = 0.5
        changed, result = account.sync_signal(updated)

        self.assertTrue(changed)
        self.assertEqual(result, "updated")
        self.assertEqual(len(account.candidates), 1)
        self.assertEqual(account.candidates["elite:1"].current_sl, 4157.0)
        self.assertEqual(account.candidates["elite:1"].remaining_fraction, 0.5)

    def test_observe_only_refuses_execution(self) -> None:
        account = PaperAccount()
        account.sync_signal(signal())
        with self.assertRaises(RuntimeError):
            account.open_position(
                signal_id="elite:1",
                fill_price=4157,
                quantity_oz=100,
                stop_loss=4164,
            )

    def test_accounting_for_sell_position(self) -> None:
        account = PaperAccount(strategy_mode="test_strategy")
        account.sync_signal(signal())
        position = account.open_position(
            signal_id="elite:1",
            fill_price=4157,
            quantity_oz=100,
            stop_loss=4164,
        )

        account.mark(4150)
        self.assertEqual(position.unrealized_pnl_usd, 700.0)
        self.assertEqual(account.equity_usd, 100700.0)

        realized = account.close_quantity(
            position_id=position.position_id,
            quantity_oz=50,
            fill_price=4150,
            reason="partial",
        )
        self.assertEqual(realized, 350.0)
        self.assertEqual(account.balance_usd, 100350.0)

        account.mark(4145)
        self.assertEqual(position.unrealized_pnl_usd, 600.0)
        self.assertEqual(account.equity_usd, 100950.0)

    def test_snapshot_round_trip_preserves_state(self) -> None:
        account = PaperAccount(strategy_mode="test_strategy")
        account.sync_signal(signal(), initial_snapshot=True)
        account.mark(4151)
        snapshot = account.snapshot()

        restored = PaperAccount.from_snapshot(snapshot)
        self.assertEqual(restored.starting_balance_usd, 100000.0)
        self.assertEqual(restored.last_mark_price, 4151.0)
        self.assertIn("elite:1", restored.candidates)
        self.assertTrue(restored.candidates["elite:1"].initial_snapshot)


if __name__ == "__main__":
    unittest.main()
