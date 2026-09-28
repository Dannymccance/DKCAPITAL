from __future__ import annotations

import unittest

from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState


class ParserTests(unittest.TestCase):
    def test_new_gold_signal(self) -> None:
        actions = parse_actions(
            "BUY XAUUSD AT 4139 - 4136\n"
            "TP1 4143\nTP2 4145.50\nTP3 4201\nSL 4130"
        )
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.symbol, "XAUUSD")
        self.assertEqual(action.direction, "BUY")
        self.assertEqual(action.entry_low, 4136)
        self.assertEqual(action.entry_high, 4139)
        self.assertEqual(action.tps[2], 4145.5)
        self.assertEqual(action.sl, 4130)

    def test_reply_tp_and_breakeven(self) -> None:
        actions = parse_actions(
            "TP1 HIT +40 PIPS ✅ SL BREAKEVEN ✅",
            reply_to_message_id=100,
        )
        self.assertEqual(actions[0].kind, "trade_update")
        self.assertEqual(actions[0].tp_hits, (1,))
        self.assertTrue(actions[0].move_sl_to_be)
        self.assertEqual(actions[0].scope, "reply")

    def test_all_gold_sl(self) -> None:
        actions = parse_actions("ALL GOLD SL TO 4272")
        self.assertEqual(actions[0].kind, "trade_update")
        self.assertEqual(actions[0].symbol, "XAUUSD")
        self.assertEqual(actions[0].sl, 4272)
        self.assertEqual(actions[0].scope, "all_matching")

    def test_both_buy_sl(self) -> None:
        actions = parse_actions(
            "BOTH SL ONE MORE TIME TO 4142 ALLOWED TO LAYER UNTIL MAX 4143.50\n"
            "LAST SL FOR BUYERS"
        )
        self.assertEqual(actions[0].kind, "trade_update")
        self.assertEqual(actions[0].direction, "BUY")
        self.assertEqual(actions[0].sl, 4142)
        self.assertEqual(actions[0].scope, "all_matching")

    def test_commentary_does_not_change_trade(self) -> None:
        actions = parse_actions(
            "On the lower time frame we can see a clear lower high structure. "
            "Buyers failed each time."
        )
        self.assertEqual(actions[0].kind, "commentary")

    def test_sl_and_be_hits_close_trades(self) -> None:
        sl = parse_actions("SL HIT", reply_to_message_id=10)
        be = parse_actions("BREAKEVEN HIT", reply_to_message_id=11)
        self.assertEqual(sl[0].kind, "stop_loss")
        self.assertEqual(be[0].kind, "breakeven_close")

    def test_reply_through_commentary_keeps_signal_context(self) -> None:
        state = SignalState()
        state.ingest_event(
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 1,
                "date": "2026-09-28T08:00:00+00:00",
                "reply_to_message_id": None,
                "text": "BUY XAUUSD AT 4200 - 4198\\nTP1 4205\\nSL 4190",
            }
        )
        state.ingest_event(
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 2,
                "date": "2026-09-28T08:01:00+00:00",
                "reply_to_message_id": 1,
                "text": "Heavy volume here, keep watching.",
            }
        )
        state.ingest_event(
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 3,
                "date": "2026-09-28T08:02:00+00:00",
                "reply_to_message_id": 2,
                "text": "SL TO 4195",
            }
        )
        self.assertEqual(state.signals["1:1"].current_sl, 4195)

    def test_reply_chain_and_global_updates(self) -> None:
        events = [
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 10,
                "date": "2026-09-28T08:00:00+00:00",
                "reply_to_message_id": None,
                "text": (
                    "BUY XAUUSD AT 4291 - 4288\n"
                    "TP1 4295\nTP2 4297.50\nTP3 4326\nSL 4282"
                ),
            },
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 11,
                "date": "2026-09-28T08:10:00+00:00",
                "reply_to_message_id": None,
                "text": (
                    "BUY XAUUSD AT 4286 - 4283\n"
                    "TP1 4290\nTP2 4292.50\nTP3 4326\nSL 4277"
                ),
            },
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 12,
                "date": "2026-09-28T08:20:00+00:00",
                "reply_to_message_id": None,
                "text": "ALL GOLD SL TO 4272",
            },
            {
                "event_type": "new_message",
                "chat_id": 1,
                "message_id": 13,
                "date": "2026-09-28T08:25:00+00:00",
                "reply_to_message_id": 11,
                "text": "TP1 HIT +40 PIPS ✅ SL BREAKEVEN ✅",
            },
        ]

        state = SignalState()
        for event in events:
            state.ingest_event(event)

        first = state.signals["1:10"]
        second = state.signals["1:11"]
        self.assertEqual(first.current_sl, 4272)
        self.assertEqual(second.current_sl, None)
        self.assertEqual(second.sl_mode, "BREAKEVEN")
        self.assertEqual(second.tp_hits, [1])


if __name__ == "__main__":
    unittest.main()
