from __future__ import annotations

import unittest

from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState


def event(
    message_id: int,
    text: str,
    *,
    minute: int,
    reply_to: int | None = None,
) -> dict:
    return {
        "event_type": "new_message",
        "chat_id": 1,
        "message_id": message_id,
        "date": f"2026-09-28T08:{minute:02d}:00+00:00",
        "reply_to_message_id": reply_to,
        "text": text,
    }


class ParserTests(unittest.TestCase):
    def test_new_gold_signal_and_duplicate_tp3_becomes_tp4(self) -> None:
        actions = parse_actions(
            "BUY XAUUSD AT 4354 - 4351\n"
            "TP1 4385\nTP2 4389.50\nTP3 4394\nTP3 4472\nSL 4333"
        )
        action = actions[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.symbol, "XAUUSD")
        self.assertEqual(action.direction, "BUY")
        self.assertEqual(action.entry_low, 4351)
        self.assertEqual(action.entry_high, 4354)
        self.assertEqual(
            action.tps,
            {1: 4385, 2: 4389.5, 3: 4394, 4: 4472},
        )
        self.assertEqual(action.sl, 4333)

    def test_correction_reply_updates_targets_and_sl(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                1,
                "BUY XAUUSD AT 4345 - 4342\n"
                "TP1 4391\nTP2 4394.50\nTP3 4398\nTP3 4472\nSL 4305",
                minute=0,
            )
        )
        state.ingest_event(
            event(
                2,
                "TP1 4401\nTP2 4403.50\nTP3 4407\nTP4 4472\nSL 4305*",
                minute=1,
                reply_to=1,
            )
        )
        signal = state.signals["1:1"]
        self.assertEqual(signal.tps[1], 4401)
        self.assertEqual(signal.tps[4], 4472)
        self.assertEqual(signal.current_sl, 4305)

    def test_reply_tp_and_breakeven(self) -> None:
        actions = parse_actions(
            "TP1 HIT +45 PIPS ✔️ SL BREAKEVEN ✔️",
            reply_to_message_id=100,
        )
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.tp_hits, (1,))
        self.assertTrue(update.move_sl_to_be)
        self.assertEqual(update.scope, "reply")

    def test_compound_tp_hits(self) -> None:
        cases = {
            "TP1&TP2 +115 PIPS ✔️": (1, 2),
            "TP1&TP2&TP3 HIT +995 PIPS ✔️": (1, 2, 3),
            "TP1, TP2, TP3 AND TP4 ARE ALL HIT +1775 PIPS": (1, 2, 3, 4),
            "DUBBLE TP1 + TP2 HIT +145 PIPS": (1, 2),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                actions = parse_actions(text, reply_to_message_id=1)
                update = next(a for a in actions if a.kind == "trade_update")
                self.assertEqual(update.tp_hits, expected)

    def test_all_gold_sl(self) -> None:
        actions = parse_actions("ALL GOLD SL TO 4272")
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.symbol, "XAUUSD")
        self.assertEqual(update.sl, 4272)
        self.assertEqual(update.scope, "all_symbol")

    def test_sl_also(self) -> None:
        actions = parse_actions("SL ALSO 4370!", reply_to_message_id=20)
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.sl, 4370)

    def test_both_sl_and_layer_max(self) -> None:
        actions = parse_actions(
            "BOTH SL TO 4365\nAllowed to layer until max 4366.50"
        )
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.sl, 4365)
        self.assertEqual(update.layer_max, 4366.5)
        self.assertEqual(update.scope, "current_group")

    def test_take_profits_all_longs_closes_all_provider_longs_only(self) -> None:
        actions = parse_actions("Take profits all longs now")
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "close")
        self.assertEqual(actions[0].direction, "BUY")
        self.assertEqual(actions[0].scope, "all_direction")

        state = SignalState()
        state.ingest_event(event(60, "BUY XAUUSD AT 4200 - 4198\\nTP1 4205\\nSL 4190", minute=0))
        state.ingest_event(event(61, "BUY BTCUSDT AT 82000 - 81900\\nTP1 83000\\nSL 81000", minute=1))
        state.ingest_event(event(62, "SELL XAUUSD AT 4210 - 4212\\nTP1 4200\\nSL 4220", minute=2))
        state.ingest_event(event(63, "Take profits all longs now", minute=3))

        self.assertEqual(state.signals["1:60"].status, "CLOSED")
        self.assertEqual(state.signals["1:61"].status, "CLOSED")
        self.assertEqual(state.signals["1:62"].status, "ACTIVE")

    def test_take_profits_all_shorts_is_provider_scoped(self) -> None:
        state = SignalState()
        state.ingest_event(event(70, "SELL XAUUSD AT 4210 - 4212\\nTP1 4200\\nSL 4220", minute=0))
        other = event(71, "SELL XAUUSD AT 4215 - 4217\\nTP1 4205\\nSL 4225", minute=1)
        other["chat_id"] = 2
        state.ingest_event(other)
        state.ingest_event(event(72, "Take profit all shorts now", minute=2))

        self.assertEqual(state.signals["1:70"].status, "CLOSED")
        self.assertEqual(state.signals["2:71"].status, "ACTIVE")

    def test_commentary_does_not_close_trade(self) -> None:
        actions = parse_actions(
            "But for now not a valid time to enter as it's already up much and we need lower"
        )
        self.assertEqual(actions[0].kind, "commentary")

    def test_contextual_cancel_only_when_replying(self) -> None:
        plain = parse_actions("Market not good now, wait I will update good signal later")
        self.assertEqual(plain[0].kind, "commentary")
        reply = parse_actions(
            "Market not good now, wait I will update good signal later",
            reply_to_message_id=5,
        )
        self.assertEqual(reply[0].kind, "cancel")

    def test_cancel_here_reply(self) -> None:
        actions = parse_actions("Alright we cancel here.", reply_to_message_id=5)
        self.assertEqual(actions[0].kind, "cancel")

    def test_add_and_reenter_language(self) -> None:
        self.assertEqual(parse_actions("ADDING NOW SAME SL")[0].kind, "add_layer")
        self.assertEqual(parse_actions("ASDING")[0].kind, "add_layer")
        self.assertEqual(parse_actions("WE RE-ENTER SAME")[0].kind, "reenter")

    def test_sl_results(self) -> None:
        cases = [
            "OUT ON SL SETUP FAILED ❌",
            "gold crashed once again SL HIT OUT.",
            "BOTH OUT ON SL Can't believe it",
            "SL OUT -140 PIPS",
        ]
        for text in cases:
            with self.subTest(text=text):
                actions = parse_actions(text)
                self.assertIn("stop_loss", {action.kind for action in actions})

    def test_reply_through_commentary_keeps_signal_context(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                1,
                "BUY XAUUSD AT 4200 - 4198\nTP1 4205\nSL 4190",
                minute=0,
            )
        )
        state.ingest_event(
            event(
                2,
                "Heavy volume here, keep watching.",
                minute=1,
                reply_to=1,
            )
        )
        state.ingest_event(
            event(3, "SL TO 4195", minute=2, reply_to=2)
        )
        self.assertEqual(state.signals["1:1"].current_sl, 4195)

    def test_group_management_updates_both_positions(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                10,
                "BUY XAUUSD AT 4383.50 - 4380.50\n"
                "TP1 4388\nTP2 4391.50\nTP3 4432\nSL 4375",
                minute=0,
            )
        )
        state.ingest_event(
            event(
                11,
                "BUY XAUUSD AT 4380 - 4377\n"
                "TP1 4384\nTP2 4386.50\nTP3 4432\nSL 4370",
                minute=7,
            )
        )
        state.ingest_event(
            event(
                12,
                "BOTH SL TO 4365\nAllowed to layer until max 4366.50",
                minute=14,
            )
        )
        first = state.signals["1:10"]
        second = state.signals["1:11"]
        self.assertEqual(first.group_id, second.group_id)
        self.assertEqual(first.current_sl, 4365)
        self.assertEqual(second.current_sl, 4365)
        self.assertEqual(first.layer_max, 4366.5)
        self.assertEqual(second.layer_max, 4366.5)

    def test_unqualified_management_uses_current_group(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                20,
                "SELL XAUUSD AT 4443 - 4446\nTP1 4439\nTP2 4436.50\nTP3 4406\nSL 4452",
                minute=0,
            )
        )
        state.ingest_event(
            event(
                21,
                "SELL XAUUSD AT 4446 - 4449\nTP1 4441\nTP2 4439.50\nTP3 4406\nSL 4455",
                minute=1,
            )
        )
        state.ingest_event(event(22, "SL TO 4460", minute=2))
        self.assertEqual(state.signals["1:20"].current_sl, 4460)
        self.assertEqual(state.signals["1:21"].current_sl, 4460)

    def test_both_out_on_sl_closes_group(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(30, "BUY XAUUSD AT 4411 - 4408\nTP1 4415\nSL 4402", minute=0)
        )
        state.ingest_event(
            event(31, "BUY XAUUSD AT 4403 - 4400\nTP1 4409\nSL 4395", minute=1)
        )
        state.ingest_event(event(32, "BOTH OUT ON SL", minute=2))
        self.assertEqual(state.signals["1:30"].status, "STOPPED")
        self.assertEqual(state.signals["1:31"].status, "STOPPED")

    def test_long_and_short_are_direction_aliases(self) -> None:
        self.assertEqual(parse_actions("cancel mubarak long", reply_to_message_id=40)[0].direction, "BUY")
        self.assertEqual(parse_actions("cancel mubarak short", reply_to_message_id=40)[0].direction, "SELL")

    def test_reply_cancel_mubarak_long_cancels_exact_replied_signal(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(80, "BUY XAUUSD AT 4143 - 4140\\nTP1 4147\\nSL 4134", minute=0)
        )
        state.ingest_event(
            event(81, "BUY BTCUSDT AT 82000 - 81900\\nTP1 83000\\nSL 81000", minute=1)
        )
        state.ingest_event(
            event(82, "cancel mubarak long", minute=2, reply_to=80)
        )
        self.assertEqual(state.signals["1:80"].status, "CANCELLED")
        self.assertEqual(state.signals["1:81"].status, "ACTIVE")
        self.assertEqual(state.message_targets[(1, 82)], ["1:80"])

    def test_reply_cancel_closes_only_referenced_signal(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(40, "BUY XAUUSD AT 4143 - 4140\nTP1 4147\nSL 4134", minute=0)
        )
        state.ingest_event(
            event(41, "Alright we cancel here.", minute=1, reply_to=40)
        )
        self.assertEqual(state.signals["1:40"].status, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
