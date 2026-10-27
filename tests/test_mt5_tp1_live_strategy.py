"""Production 8% TP1 strategy contracts, without authorising live execution."""
from __future__ import annotations

import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dkcapital.mt5_gateway import GatewayConfig, Mt5GatewayHandler
from dkcapital.mt5_tp1_policy import (
    STORM_CHAT_ID, CAPITAL_CHAT_ID, attested_tp1_risk,
    tp1_block_reason, uk_entry_window,
)
from dkcapital.signal_state import SignalState
from scripts.generate_mt5_tp1_live import tp1_source


def current(at: str = "2026-10-27T08:15:00+00:00") -> datetime:
    return datetime.fromisoformat(at)


def snapshot() -> dict:
    return {
        "mode": "REAL", "currency": "USD", "balance": 1000,
        "equity": 1100, "credit": 300, "risk_schedule": "TP1_V1",
        "signal_risk_cap_pct": 8.0, "portfolio_risk_cap_account_currency": 64.0,
        "bid": 4000, "ask": 4000.2, "positions": [],
        "unmanaged_positions_count": 0, "broker_orders_total": 0,
    }


def incoming(chat_id: int = STORM_CHAT_ID, direction: str = "BUY") -> dict:
    return {
        "signal_id": f"{chat_id}:22", "chat_id": chat_id,
        "origin_chat_id": chat_id, "source_style": "gws",
        "order_kind": "MARKET_NOW", "symbol": "XAUUSD",
        "status": "ACTIVE", "direction": direction,
        "entry_low": 3999.5, "entry_high": 4001.0,
        "tps": {"1": 4004.0} if direction == "BUY" else {"1": 3994.0},
        "current_sl": 3993 if direction == "BUY" else 4005,
        "entry_ready_at": "2026-10-27T08:14:45+00:00",
    }


class Tp1PolicyTests(unittest.TestCase):
    def test_8pct_broker_attestation_excludes_credit(self):
        self.assertTrue(attested_tp1_risk(snapshot()))
        for change in (
            {"risk_schedule": "UK_V2"},
            {"signal_risk_cap_pct": 1.0},
            {"portfolio_risk_cap_account_currency": 100},
            {"credit": 0},
            {"mode": "DEMO"},
            {"currency": "GBP"},
        ):
            with self.subTest(change=change):
                self.assertFalse(attested_tp1_risk({**snapshot(), **change}))

    def test_uk_session_cutoff_and_dst(self):
        for iso, allowed in (
            ("2026-10-27T00:00:00+00:00", True),
            ("2026-10-27T14:59:59+00:00", True),
            ("2026-10-27T15:00:00+00:00", False),
            ("2026-07-01T13:59:59+00:00", True),
            ("2026-07-01T14:00:00+00:00", False),
        ):
            with self.subTest(iso=iso):
                self.assertEqual(uk_entry_window(current(iso)), allowed)

    def test_pinned_storm_capital_and_forwarded_origins(self):
        now=current()
        self.assertIsNone(tp1_block_reason(incoming(STORM_CHAT_ID), snapshot(), now=now))
        self.assertIsNone(tp1_block_reason(incoming(CAPITAL_CHAT_ID), snapshot(), now=now))
        self.assertEqual(tp1_block_reason(incoming(-1004359492110), snapshot(), now=now),
                         "PROVIDER_DISABLED")
        forged = {**incoming(), "origin_chat_id": -1004359492110}
        self.assertEqual(tp1_block_reason(forged, snapshot(), now=now), "PROVIDER_DISABLED")
        unknown = {**incoming(), "origin_chat_id": 0}
        self.assertEqual(tp1_block_reason(unknown, snapshot(), now=now), "PROVIDER_DISABLED")

    def test_now_market_vs_limit_and_entry_deviation(self):
        s=incoming()
        self.assertEqual(tp1_block_reason({**s, "order_kind": "LIMIT"}, snapshot(), now=current()),
                         "LIMIT_ORDER_NOT_CONVERTED")
        self.assertEqual(tp1_block_reason({**s, "entry_low": 4020, "entry_high": 4021},
                                          snapshot(), now=current()), "ENTRY_DEVIATION_EXCEEDED")
        unquoted = {**s, "entry_low": 0, "entry_high": 0}
        self.assertIsNone(tp1_block_reason(unquoted, snapshot(), now=current()))
        self.assertEqual(tp1_block_reason({**s, "tps": {"1": 4000.1}},
                                          snapshot(), now=current()), "TP1_ALREADY_PASSED_OR_INVALID")
        self.assertEqual(tp1_block_reason({**s, "current_sl": None},
                                          snapshot(), now=current()), "PROVIDER_SL_MISSING")
        self.assertEqual(tp1_block_reason({**s, "entry_ready_at": "2026-10-27T08:05:00+00:00"},
                                          snapshot(), now=current()), "ENTRY_WINDOW_EXPIRED")

    def test_broker_position_conflicts_and_unmanaged(self):
        self.assertEqual(tp1_block_reason(incoming(), {**snapshot(), "positions": [
            {"side": "SELL"}]}, now=current()), "OPPOSING_POSITION_ACTIVE")
        self.assertEqual(tp1_block_reason(incoming(), {**snapshot(),
            "unmanaged_positions_count": 1}, now=current()), "UNMANAGED_BROKER_EXPOSURE")

    def test_edit_can_make_unquoted_now_executable_without_backdating(self):
        state=SignalState()
        event={
            "event_type": "new_message", "chat_id": STORM_CHAT_ID,
            "message_id": 55, "date": "2026-10-27T08:00:00+00:00",
            "text": "XAUUSD BUY NOW", "reply_to_message_id": None,
        }
        state.ingest_event(event)
        self.assertEqual(state.snapshot()["signals"], [])

        amended={**event, "event_type": "message_edited",
                 "edit_date": "2026-10-27T08:14:50+00:00",
                 "text": "XAUUSD BUY NOW TP1 4004 SL 3993"}
        state.ingest_event(amended)
        ready=state.snapshot()["signals"][0]
        self.assertEqual(ready["entry_ready_at"], amended["edit_date"])
        self.assertEqual(ready["opened_at"], event["date"])
        self.assertEqual(ready["tps"]["1"], 4004.0)
        self.assertEqual(ready["current_sl"], 3993.0)

    def test_generated_broker_ea_is_live_8pct_single_leg_and_disarmed(self):
        src=tp1_source(3, 710003, "REAL", "USD", "Vantage-Live", 130)
        for token in (
            "DKACCOUNT3V5", 'MaxSignalRiskPct = 8.0',
            'MaxPortfolioRiskAccountCurrency = 80.0',
            'MaxGroupRiskAccountCurrency = 80.0',
            'input bool EnableNewEntries = false;',
            'UKTradingHour()>=15', 'targets[t]=0;',
            'OrderCalcProfit(', 'OrderCalcMargin(', 'TP1_V1',
            'if(true) return; // TP1 strategy never layers.',
            'BROKER_ORDER_REJECTED_OR_PARTIAL',
        ):
            with self.subTest(token=token):
                self.assertIn(token,src)
        self.assertIn('for(int i=0;i<initialLegCount;i++)',src)
        self.assertIn('double targetLots=FloorLots(',src)
        self.assertNotIn('return hour>=1 && hour<9 ? 10.0 : 4.0;',src)
        with self.assertRaises(ValueError):
            tp1_source(2, 710003, "REAL", "USD", "Vantage-Live",130)


if __name__ == "__main__":
    unittest.main()
