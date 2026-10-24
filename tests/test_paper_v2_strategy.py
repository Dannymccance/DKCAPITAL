"""Deterministic paper V2 trading tests; no broker orders or resets."""
from __future__ import annotations

import unittest
import math
from datetime import UTC, datetime, timedelta

from dkcapital.paper_trading import PaperAccount
from dkcapital.paper_v2_strategy import V2PaperStrategy
from dkcapital.xau_strategy import XauStrategyConfig


T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
STORM = -1004334637102


def when(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def quote_at(fill: float, seconds: int, direction: str = "BUY") -> dict:
    bid, ask = (fill - 0.2, fill) if direction == "BUY" else (fill, fill + 0.2)
    return {
        "price": (bid + ask) / 2,
        "bid": bid, "ask": ask, "computed_at": when(seconds),
        "source": "delayed-test-tape",
    }


def signal(*, chat: int = STORM, sl: float | None = 3994,
           tp3_open: bool = False, tps: dict | None = None,
           first_seen: int = 5, history: list | None = None) -> dict:
    return {
        "signal_id": f"{chat}:101", "chat_id": chat, "group_id": f"{chat}:101",
        "symbol": "XAUUSD", "source_style": "gws", "root_message_id": 101,
        "direction": "BUY", "opened_at": when(0), "status": "ACTIVE",
        "entry_low": 4001, "entry_high": 4003,
        "original_sl": sl, "current_sl": sl, "sl_mode": "PRICE",
        "tps": tps if tps is not None else {"1": 4005, "2": 4010, "3": 4020},
        "tp3_open": tp3_open, "be_deferred": False,
        "history": history if history is not None else [{
            "kind": "opened", "timestamp": when(0), "message_id": 101
        }],
    }


class V2PaperTests(unittest.TestCase):
    def setUp(self):
        self.account = PaperAccount(
            starting_balance_usd=100_000, strategy_mode="signal_follow_v2",
        )
        self.account.activate_strategy("signal_follow_v2", activated_at=when(-30))
        self.strategy = V2PaperStrategy(XauStrategyConfig())

    def add(self, item=None, *, seen=5):
        self.account.sync_signal(item or signal(), observed_at=when(seen))

    def process(self, fill: float, seconds: int):
        return self.strategy.process(self.account, quote_at(fill, seconds),
                                     timestamp=when(seconds))

    def test_storm_waits_for_price_zone_two_minutes_later(self):
        self.add()
        self.process(3998, 15)
        self.assertNotIn(f"paper:{STORM}:101", self.account.positions)
        self.assertEqual(self.account.candidates[f"{STORM}:101"].execution_status,
                         "PENDING_STRATEGY")
        self.process(4001.5, 120)
        self.assertIn(f"paper:{STORM}:101", self.account.positions)
        position = self.account.positions[f"paper:{STORM}:101"]
        self.assertEqual(position.stop_source, "PROVIDER")
        self.assertTrue(position.v2_storm_policy)
        self.assertEqual(position.tp_prices["1"], 4010)  # STORM TP2 initially
        self.assertLessEqual(position.initial_risk_usd, 4000)
        self.assertGreaterEqual(position.v2_original_lots, 0.03)

    def test_storm_rejects_late_receipt_and_expired_unfilled_zone(self):
        self.add(signal(), seen=50)
        self.process(4001.5, 120)
        self.assertEqual(self.account.candidates[f"{STORM}:101"].execution_status,
                         "V2_STALE_ON_RECEIPT")
        self.assertFalse(self.account.positions)

        account = PaperAccount(strategy_mode="signal_follow_v2")
        account.activate_strategy("signal_follow_v2", activated_at=when(-30))
        account.sync_signal(signal(), observed_at=when(5))
        self.strategy.process(account, quote_at(3998, 301), timestamp=when(301))
        self.assertEqual(account.candidates[f"{STORM}:101"].execution_status,
                         "V2_ZONE_TIMEOUT")
        self.assertFalse(account.positions)

    def test_all_gws_normal_freshness_three_minutes(self):
        other = -1004406990046
        self.add(signal(chat=other), seen=4)
        self.process(3998, 100)
        self.process(4001.5, 150)
        self.assertIn(f"paper:{other}:101", self.account.positions)

    def test_requires_provider_sl_and_native_tp(self):
        self.add(signal(sl=None))
        self.process(4001.5, 30)
        self.assertEqual(self.account.candidates[f"{STORM}:101"].execution_status,
                         "V2_PROVIDER_SL_INVALID")
        self.assertFalse(self.account.positions)

        a = PaperAccount(strategy_mode="signal_follow_v2")
        a.activate_strategy("signal_follow_v2", activated_at=when(-30))
        a.sync_signal(signal(tps={"1": 4005, "2": 4010}), observed_at=when(5))
        self.strategy.process(a, quote_at(4001.5, 30), timestamp=when(30))
        self.assertEqual(a.candidates[f"{STORM}:101"].execution_status,
                         "V2_REQUIRED_TP_MISSING")

    def test_explicit_open_runner_preserves_last_40_percent(self):
        self.add(signal(tp3_open=True, tps={"1": 4005, "2": 4010}))
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        self.assertTrue(p.v2_open_runner)
        self.assertNotIn("3", p.tp_prices)
        self.process(4010.5, 40)  # closes TP1 + TP2 at provider TP2
        self.assertEqual(p.status, "OPEN")
        self.assertAlmostEqual(p.remaining_quantity_oz, p.initial_quantity_oz * .4, places=5)
        self.process(4100.0, 70)
        self.assertEqual(p.status, "OPEN")

    def test_provider_tp_hit_does_not_fake_close_or_auto_be(self):
        self.add()
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        qty = p.remaining_quantity_oz
        update = signal(history=[
            {"kind": "opened", "timestamp": when(0), "message_id": 101},
            {"kind": "trade_update", "timestamp": when(35), "message_id": 102,
             "tp_hits": [1]},
        ])
        self.add(update, seen=35)
        self.process(4002.0, 40)
        self.assertEqual(p.remaining_quantity_oz, qty)
        self.assertEqual(p.stop_loss, 3994)
        self.assertEqual(p.tp_hits, [])

    def test_storm_adverse_drawdown_turns_on_real_tp1(self):
        self.add()
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        self.process(3999.3, 30)   # 2.2 adverse before SL
        self.assertTrue(p.v2_storm_drawdown_seen)
        self.assertEqual(p.tp_prices["1"], 4005)
        self.process(4005.3, 40)
        self.assertIn(1, p.tp_hits)
        self.assertEqual(p.stop_loss, p.entry_price)

    def test_no_breakeven_yet_suppresses_automatic_tp1_but_explicit_be_works(self):
        self.add(signal(tp3_open=True, tps={"1": 4005, "2": 4010}))
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        no_be = signal(tp3_open=True, tps={"1": 4005, "2": 4010})
        no_be["be_deferred"] = True
        no_be["history"].append({
            "kind": "trade_update", "message_id": 105,
            "timestamp": when(15), "defer_breakeven": True,
        })
        self.add(no_be, seen=15)
        self.process(4010.2, 30)
        self.assertIn(1, p.tp_hits)
        self.assertEqual(p.stop_loss, 3994)
        self.assertFalse(p.breakeven_locked)
        confirm = signal(tp3_open=True, tps={"1": 4005, "2": 4010})
        confirm["history"].append({
            "kind": "trade_update", "message_id": 105,
            "timestamp": when(15), "defer_breakeven": True,
        })
        confirm["history"].append({
            "kind": "trade_update", "message_id": 106,
            "timestamp": when(40), "move_sl_to_be": True,
        })
        confirm["sl_mode"] = "BREAKEVEN"
        self.add(confirm, seen=40)
        self.process(4010.2, 50)
        self.assertEqual(p.stop_loss, p.entry_price)

    def test_daily_loss_alert_only_and_account_snapshot_roundtrip(self):
        self.add()
        self.account.daily_stop_triggered = True
        self.process(4001.5, 20)
        self.assertIn(f"paper:{STORM}:101", self.account.positions)
        before = self.account.snapshot()
        restored = PaperAccount.from_snapshot(before)
        self.assertEqual(restored.balance_usd, self.account.balance_usd)
        self.assertEqual(restored.positions[f"paper:{STORM}:101"].v2_original_lots,
                         self.account.positions[f"paper:{STORM}:101"].v2_original_lots)

    def test_layers_are_not_inferred_from_results(self):
        self.add()
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        original_lots = p.lot_size
        update = signal()
        update["history"].append({
            "kind": "add_layer", "message_id": 110, "timestamp": when(12),
            "raw_text": "+LAYERS; TP1 HIT 45 PIPS",
        })
        self.add(update, seen=15)
        self.process(4000.8, 20)
        self.assertEqual(p.lot_size, original_lots)
        self.assertFalse(p.v2_layer_attempted)

    def test_explicit_layer_is_one_10_percent_and_allocated_to_tp2(self):
        self.add()
        self.process(4001.5, 10)
        p = self.account.positions[f"paper:{STORM}:101"]
        original_lots = p.lot_size
        original_tp2 = p.tp_close_quantities_oz["2"]
        addition = signal()
        addition["history"].append({
            "kind": "add_layer", "message_id": 110, "timestamp": when(15),
            "raw_text": "ADDING NOW SAME SL", "layer_max": None,
        })
        self.add(addition, seen=15)
        self.process(4001.0, 20)  # improvement vs original broker ask 4001.5
        self.assertTrue(p.v2_layer_attempted)
        layer = p.lot_size - original_lots
        expected = math.floor(original_lots * 0.1 / .01 + 1e-9) * .01
        self.assertAlmostEqual(layer, expected, places=6)
        self.assertAlmostEqual(
            p.tp_close_quantities_oz["2"] - original_tp2, layer * 100, places=6,
        )


if __name__ == "__main__":
    unittest.main()
