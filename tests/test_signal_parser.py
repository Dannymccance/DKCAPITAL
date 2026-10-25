from __future__ import annotations

import unittest

from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState


def event(
    message_id: int,
    text: str,
    *,
    minute: int,
    second: int = 0,
    reply_to: int | None = None,
) -> dict:
    return {
        "event_type": "new_message",
        "chat_id": 1,
        "message_id": message_id,
        "date": f"2026-09-28T08:{minute:02d}:{second:02d}+00:00",
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

    def test_capital_actual_gold_shorthand_entry(self) -> None:
        action = parse_actions(
            "XAUUSD 4084-85 BUY\nTP1 : 4090\nTP2 : 4098\nTP3: OPEN\nSL : 4079"
        )[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual((action.entry_low, action.entry_high), (4084.0, 4085.0))
        self.assertEqual(action.direction, "BUY")
        self.assertEqual(action.tps, {1: 4090, 2: 4098})
        self.assertEqual(action.sl, 4079)

    def test_gws_two_target_signal_does_not_invent_tp3(self) -> None:
        message = (
            "GWS - CAPITAL\n"
            "XAUUSD 4003 - 4000: Buy\n"
            "TP1: 4107\n"
            "TP2: 4120\n"
            "SL: 3996"
        )
        parsed = parse_actions(message)[0]
        self.assertEqual(parsed.kind, "new_signal")
        self.assertEqual((parsed.entry_low, parsed.entry_high), (4000, 4003))
        self.assertEqual(parsed.direction, "BUY")
        self.assertEqual(parsed.sl, 3996)
        self.assertEqual(parsed.tps, {1: 4107, 2: 4120})
        self.assertFalse(parsed.tp3_open)
        state = SignalState()
        state.ingest_event(event(63, message, minute=0))
        stored = state.signals["1:63"]
        self.assertEqual(stored.tps, {1: 4107, 2: 4120})
        self.assertFalse(stored.tp3_open)

    def test_four_target_telegram_is_retained_by_parser_and_state(self) -> None:
        original = ("GWS - CAPITAL\\n"
                    "XAUUSD 4003 - 4000: Buy\\n"
                    "TP1: 4010\\nTP2: 4020\\nTP3: 4030\\nTP4: 4040\\nSL: 3996")
        original = original.replace("\\n", "\n")
        action = parse_actions(original)[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.tps, {1: 4010, 2: 4020, 3: 4030, 4: 4040})
        state = SignalState()
        state.ingest_event(event(64, original, minute=0))
        self.assertEqual(state.signals["1:64"].tps,
                         {1: 4010, 2: 4020, 3: 4030, 4: 4040})

    def test_six_targets_remain_distinct_in_parser_and_state(self) -> None:
        message = ("GWS - CAPITAL\nXAUUSD 4003 - 4000: Buy\n"
                   + "\n".join(f"TP{i}: {4000+i*10}" for i in range(1, 7))
                   + "\nSL: 3996")
        action = parse_actions(message)[0]
        self.assertEqual(action.kind, "new_signal")
        expected = {i: 4000+i*10 for i in range(1, 7)}
        self.assertEqual(action.tps, expected)
        state = SignalState()
        state.ingest_event(event(65, message, minute=0))
        self.assertEqual(state.signals["1:65"].tps, expected)

    def test_capital_explicit_open_runner_persists_without_invented_tp(self) -> None:
        original = ("XAUUSD 4084-85 BUY\nTP1: 4090\nTP2: 4098\n"
                    "TP3: OPEN\nSL: 4079")
        action = parse_actions(original)[0]
        self.assertTrue(action.tp3_open)
        self.assertEqual(action.tps, {1: 4090, 2: 4098})
        state = SignalState()
        state.ingest_event(event(60, original, minute=0))
        signal = state.signals["1:60"]
        self.assertTrue(signal.tp3_open)
        self.assertNotIn(3, signal.tps)
        # A later explicit third numeric TP replaces the OPEN instruction.
        state.ingest_event(event(61, "TP3: 4104", minute=1, reply_to=60))
        self.assertFalse(state.signals["1:60"].tp3_open)
        self.assertEqual(state.signals["1:60"].tps, {1: 4090, 2: 4098, 3: 4104})

    def test_provider_no_breakeven_yet_survives_tp1_report(self) -> None:
        signal_text = ("BUY XAUUSD AT 4150 - 4148\nTP1 4155\n"
                       "TP2 4160\nTP3 4165\nSL 4140")
        state = SignalState()
        state.ingest_event(event(70, signal_text, minute=0))
        directive = "TP1 HIT +45 PIPS\nNo breakeven yet if it goes back enter again if not hold"
        actions = parse_actions(directive, reply_to_message_id=70)
        self.assertFalse(any(a.move_sl_to_be for a in actions))
        self.assertTrue(any(a.defer_breakeven for a in actions))
        state.ingest_event(event(71, directive, minute=1, reply_to=70))
        signal = state.signals["1:70"]
        self.assertTrue(signal.be_deferred)
        self.assertEqual(signal.sl_mode, "PRICE")
        self.assertEqual(signal.current_sl, 4140)
        state.ingest_event(event(72, "SET ALL BREAKEVEN NOW", minute=2))
        signal = state.signals["1:70"]
        self.assertFalse(signal.be_deferred)
        self.assertEqual(signal.sl_mode, "BREAKEVEN")

    def test_explicit_all_be_covers_both_provider_sides_but_not_other_chat(self) -> None:
        state = SignalState()
        state.ingest_event(event(1,
            "BUY XAUUSD AT 4150 - 4148\nTP1 4155\nTP2 4160\nTP3 4165\nSL 4140",
            minute=0,
        ))
        state.ingest_event(event(2,
            "SELL XAUUSD AT 4155 - 4158\nTP1 4148\nTP2 4145\nTP3 4140\nSL 4165",
            minute=1,
        ))
        state.ingest_event({
            **event(1,
                "SELL XAUUSD AT 4155 - 4158\nTP1 4148\nTP2 4145\nTP3 4140\nSL 4165",
                minute=1),
            "chat_id": 2,
        })
        state.ingest_event(event(3, "SETTY ALL BREAKEVEN NOWW", minute=2))
        self.assertEqual(state.signals["1:1"].sl_mode, "BREAKEVEN")
        self.assertEqual(state.signals["1:2"].sl_mode, "BREAKEVEN")
        self.assertEqual(state.signals["2:1"].sl_mode, "PRICE")

    def test_no_be_comment_does_not_remove_earlier_broker_protection(self) -> None:
        state = SignalState()
        state.ingest_event(event(80,
            "BUY XAUUSD AT 4150\nTP1 4155\nTP2 4160\nTP3 4165\nSL 4140",
            minute=0,
        ))
        state.ingest_event(event(81, "SET BE", minute=1, reply_to=80))
        state.ingest_event(event(82, "NO BREAKEVEN YET", minute=2, reply_to=80))
        sig = state.signals["1:80"]
        self.assertEqual(sig.sl_mode, "BREAKEVEN")
        self.assertFalse(sig.be_deferred)

    def test_capital_reverse_shorthand_and_full_price(self) -> None:
        cases = [
            ("XAUUSD 4121-20 BUY\nTP1: 4125\nSL: 4115", (4120, 4121)),
            ("XAUUSD 4132-4133 SELL\nTP1: 4128\nSL: 4138", (4132, 4133)),
            ("XAUUSD 4049BUY\nTP1: 4054\nSL: 4044", (4049, 4049)),
            ("XAUUSD 4003 - 4000: Buy\nTP1: 4107\nSL: 3996", (4000, 4003)),
            ("XAUUSD 4099-01 BUY\nTP1 4105\nSL 4090", (4099, 4101)),
        ]
        for message, prices in cases:
            with self.subTest(message=message):
                action = parse_actions(message)[0]
                self.assertEqual(action.kind, "new_signal")
                self.assertEqual((action.entry_low, action.entry_high), prices)

    def test_storm_actual_unnumbered_targets_and_short_zone(self) -> None:
        cases = [
            ("XAU USD BUY NOW 4129.5 - 4125.5\n"
             "TP 4131.5\nTP 4132.5\nTP 4133.5\nTP 4136.5\nSL 4123.5",
             (4125.5, 4129.5), {1: 4131.5, 2: 4132.5, 3: 4133.5, 4: 4136.5}),
            ("XAU USD SELL NOW 4082-86\n"
             "TP 4080\nSL 4087",
             (4082, 4086), {1: 4080}),
            ("XAU USD SELL NOW 4051.5 - 4055.5\n"
             "TP 4049,5\nTP 4048.5\nTP 4047\nSL 4057",
             (4051.5, 4055.5), {1: 4049.5, 2: 4048.5, 3: 4047}),
            ("Buy Gold Now 4132 - 4126\n"
             "TP 4135.5\nTP 4137.5\nTP 4140\nSL 4123",
             (4126, 4132), {1: 4135.5, 2: 4137.5, 3: 4140}),
        ]
        for message, prices, tps in cases:
            with self.subTest(message=message):
                action = parse_actions(message)[0]
                self.assertEqual(action.kind, "new_signal")
                self.assertEqual((action.entry_low, action.entry_high), prices)
                self.assertEqual(action.tps, tps)

    def test_price_mentions_and_far_suffix_are_not_trades(self) -> None:
        self.assertEqual(
            parse_actions("XAUUSD 4084-20 BUY\nTP1 4090\nSL 4079")[0].kind,
            "unparsed_new_signal",
        )
        for message in (
            "XAUUSD Target Price 1: +60 Pips.",
            "I will buy again with an SL of 4096",
        ):
            with self.subTest(message=message):
                self.assertEqual(parse_actions(message)[0].kind, "commentary")

    def test_close_partials_with_set_be_does_not_close_entire_trade(self) -> None:
        actions = parse_actions("Close partials, set BE")
        self.assertNotIn("close", {a.kind for a in actions})
        updates = [a for a in actions if a.kind == "trade_update"]
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0].move_sl_to_be)

    def test_layer_permission_and_tp_recap_do_not_open_a_layer(self) -> None:
        for message in (
            "ALLOWED TO LAYER UNTIL MAX 4036.50",
            "TP1 HIT +50 PIPS\nSL BREAKEVEN\n+LAYERS",
            "If you layered the zone, make your trade risk free",
        ):
            with self.subTest(message=message):
                self.assertNotIn("add_layer", {a.kind for a in parse_actions(message)})

    def test_rapid_same_direction_signal_without_correction_evidence_is_separate(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                90,
                "SELL XAUUSD AT 4168 - 4171\n"
                "TP1 4164\nTP2 4161.50\nTP3 4141\nSL 4377",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            event(
                91,
                "SELL XAUUSD AT 4169.50 - 4172.50\n"
                "TP1 4165\nTP2 4162.50\nTP3 4141\nSL 4378",
                minute=0,
                second=20,
            )
        )

        self.assertEqual(set(state.signals), {"1:90", "1:91"})

    def test_explicit_correction_within_30_seconds_amends_existing_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                100,
                "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nSL 4177",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            event(
                101,
                "UPDATED SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nSL 4178",
                minute=0,
                second=20,
            )
        )

        self.assertEqual(list(state.signals), ["1:100"])
        signal = state.signals["1:100"]
        self.assertEqual(signal.entry_low, 4169.5)
        self.assertEqual(signal.entry_high, 4172.5)
        self.assertEqual(signal.current_sl, 4178.0)
        self.assertEqual(signal.source_message_ids, [100, 101])
        self.assertEqual(signal.history[-1]["kind"], "signal_amendment")

    def test_exact_duplicate_within_30_seconds_is_deduplicated(self) -> None:
        state = SignalState()
        text = "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nSL 4177"
        state.ingest_event(event(102, text, minute=0, second=0))
        state.ingest_event(event(103, text, minute=0, second=10))

        self.assertEqual(list(state.signals), ["1:102"])
        self.assertEqual(state.signals["1:102"].source_message_ids, [102, 103])

    def test_explicit_buy_more_full_signal_is_separate_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                104,
                "BUY XAUUSD AT 4160 - 4162\nTP1 4170\nSL 4152",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            event(
                105,
                "BUY MORE XAUUSD AT 4155 - 4157\nTP1 4165\nSL 4148",
                minute=0,
                second=15,
            )
        )

        self.assertEqual(set(state.signals), {"1:104", "1:105"})

    def test_deleted_signal_repost_within_30_seconds_amends_original_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                96,
                "SELL XAUUSD AT 4168 - 4171\n"
                "TP1 4164\nTP2 4161.50\nTP3 4141\nSL 4377",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            {
                "event_type": "message_deleted",
                "chat_id": 1,
                "message_ids": [96],
                "observed_at": "2026-09-28T08:00:10+00:00",
            }
        )
        state.ingest_event(
            event(
                97,
                "SELL XAUUSD AT 4169.50 - 4172.50\n"
                "TP1 4165\nTP2 4162.50\nTP3 4141\nSL 4378",
                minute=0,
                second=20,
            )
        )

        self.assertEqual(list(state.signals), ["1:96"])
        signal = state.signals["1:96"]
        self.assertEqual(signal.entry_low, 4169.5)
        self.assertEqual(signal.entry_high, 4172.5)
        self.assertEqual(signal.current_sl, 4378.0)
        self.assertEqual(signal.source_message_ids, [96, 97])
        self.assertEqual(signal.history[-1]["kind"], "signal_amendment")
        self.assertEqual(state.message_targets[(1, 97)], ["1:96"])

    def test_deleted_signal_repost_at_51_seconds_amends_original_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                108,
                "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nTP2 4161.50\nTP3 4141\nSL 4377",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            {
                "event_type": "message_deleted",
                "chat_id": 1,
                "message_ids": [108],
                "observed_at": "2026-09-28T08:00:10+00:00",
            }
        )
        state.ingest_event(
            event(
                109,
                "SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nTP2 4162.50\nTP3 4141\nSL 4378",
                minute=0,
                second=51,
            )
        )

        self.assertEqual(list(state.signals), ["1:108"])
        signal = state.signals["1:108"]
        self.assertEqual(signal.entry_low, 4169.5)
        self.assertEqual(signal.entry_high, 4172.5)
        self.assertEqual(signal.current_sl, 4378.0)
        self.assertEqual(signal.source_message_ids, [108, 109])
        self.assertEqual(signal.history[-1]["kind"], "signal_amendment")

    def test_deleted_signal_repost_after_90_seconds_is_separate_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                110,
                "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nSL 4177",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            {
                "event_type": "message_deleted",
                "chat_id": 1,
                "message_ids": [110],
                "observed_at": "2026-09-28T08:00:10+00:00",
            }
        )
        state.ingest_event(
            event(
                111,
                "SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nSL 4178",
                minute=1,
                second=31,
            )
        )

        self.assertEqual(list(state.signals), ["1:111"])

    def test_deleted_signal_followed_by_explicit_scale_in_is_not_amendment(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                106,
                "BUY XAUUSD AT 4160 - 4162\nTP1 4170\nSL 4152",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            {
                "event_type": "message_deleted",
                "chat_id": 1,
                "message_ids": [106],
                "observed_at": "2026-09-28T08:00:05+00:00",
            }
        )
        state.ingest_event(
            event(
                107,
                "BUY MORE XAUUSD AT 4155 - 4157\nTP1 4165\nSL 4148",
                minute=0,
                second=15,
            )
        )

        self.assertEqual(list(state.signals), ["1:107"])

    def test_deleted_repost_amendment_survives_event_log_rebuild(self) -> None:
        state = SignalState()
        events = [
            event(
                98,
                "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nSL 4377",
                minute=0,
                second=0,
            ),
            {
                "event_type": "message_deleted",
                "chat_id": 1,
                "message_ids": [98],
                "observed_at": "2026-09-28T08:00:05+00:00",
            },
            event(
                99,
                "SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nSL 4378",
                minute=0,
                second=25,
            ),
        ]
        for item in events:
            state.ingest_event(item, rebuild=False)
        state.rebuild()

        self.assertEqual(list(state.signals), ["1:98"])
        self.assertEqual(state.signals["1:98"].source_message_ids, [98, 99])
        self.assertEqual(state.signals["1:98"].current_sl, 4378.0)

    def test_signal_after_30_seconds_remains_separate_trade(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                92,
                "SELL XAUUSD AT 4168 - 4171\nTP1 4164\nSL 4177",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            event(
                93,
                "SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nSL 4178",
                minute=0,
                second=31,
            )
        )

        self.assertEqual(set(state.signals), {"1:92", "1:93"})

    def test_opposite_direction_within_30_seconds_is_not_amendment(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                94,
                "BUY XAUUSD AT 4168 - 4171\nTP1 4180\nSL 4160",
                minute=0,
                second=0,
            )
        )
        state.ingest_event(
            event(
                95,
                "SELL XAUUSD AT 4169.50 - 4172.50\nTP1 4165\nSL 4178",
                minute=0,
                second=10,
            )
        )

        self.assertEqual(set(state.signals), {"1:94", "1:95"})

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
        self.assertEqual(parse_actions("BUY MORE AT BETTER ENTRY")[0].kind, "add_layer")
        self.assertEqual(parse_actions("SELL MORE AT BETTER ENTRY")[0].kind, "add_layer")
        self.assertEqual(parse_actions("WE RE-ENTER SAME")[0].kind, "reenter")


    def test_gws_adds_is_single_explicit_add_not_permission(self) -> None:
        state = SignalState()
        state.ingest_event(event(100, "BUY XAUUSD AT 4169 - 4166\nTP1 4173\nSL 4160", minute=0))
        state.ingest_event(event(101, "BUY XAUUSD AT 4165 - 4162\nTP1 4169\nSL 4155", minute=1))
        state.ingest_event(event(102, "ALLOWED TO LAYER UNTIL MAX 4150", minute=2))
        self.assertEqual(state.signals["1:100"].layers, 1)
        self.assertEqual(state.signals["1:101"].layers, 1)
        state.ingest_event(event(103, "ADDS", minute=3))
        self.assertEqual(state.signals["1:100"].layers, 1)
        self.assertEqual(state.signals["1:101"].layers, 2)
        add = [h for h in state.signals["1:101"].history if h["kind"] == "add_layer"]
        self.assertEqual(len(add), 1)
        self.assertEqual(add[0]["layer_max"], 4150)
        self.assertEqual(state.signals["1:100"].group_id, state.signals["1:101"].group_id)

    def test_ambiguous_layer_reply_to_group_management_is_blocked(self) -> None:
        state = SignalState()
        state.ingest_event(event(110, "BUY XAUUSD AT 4169 - 4166\nSL 4160", minute=0))
        state.ingest_event(event(111, "BUY XAUUSD AT 4165 - 4162\nSL 4155", minute=1))
        state.ingest_event(event(112, "BOTH SL TO 4155", minute=2))
        state.ingest_event(event(113, "ADDS", minute=3, reply_to=112))
        self.assertEqual(state.signals["1:110"].layers, 1)
        self.assertEqual(state.signals["1:111"].layers, 1)
        self.assertEqual(len(state.unresolved_actions), 1)
        self.assertEqual(state.unresolved_actions[0]["reason"], "ambiguous_layer_target")

    def test_buy_more_full_signal_parses_as_new_signal(self) -> None:
        action = parse_actions(
            "BUY MORE XAUUSD AT 4155 - 4157\nTP1 4165\nSL 4148"
        )[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.direction, "BUY")
        self.assertEqual(action.entry_low, 4155.0)
        self.assertEqual(action.entry_high, 4157.0)

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


    def test_gws_move_both_sl_and_layer_permission(self) -> None:
        state = SignalState()
        state.ingest_event(event(100, "BUY XAUUSD AT 4169 - 4166\\nSL 4160", minute=0))
        state.ingest_event(event(101, "BUY XAUUSD AT 4164 - 4161\\nSL 4155", minute=1))
        state.ingest_event(event(102, "WE MOVE BOTH SL TO 4148.50 (Last SL for buyers)", minute=2))
        self.assertEqual(state.signals["1:100"].current_sl, 4148.5)
        self.assertEqual(state.signals["1:101"].current_sl, 4148.5)
        state.ingest_event(event(103, "ALLOWED TO LAYER UNTIL MAX 4150", minute=3))
        self.assertEqual(state.signals["1:100"].layer_max, 4150)
        self.assertEqual(state.signals["1:101"].layer_max, 4150)
        # Permission to layer must not itself create new signals or broker entries.
        self.assertEqual(len(state.signals), 2)
        self.assertEqual(state.signals["1:100"].layers, 1)
        self.assertEqual(state.signals["1:101"].layers, 1)

    def test_gws_successive_standalone_sl_widening_amendments(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(110, "BUY XAUUSD AT 4074 - 4071\nTP1 4078\nSL 4065", minute=0)
        )
        state.ingest_event(event(111, "SL ALSO 4060", minute=1, reply_to=110))
        self.assertEqual(state.signals["1:110"].current_sl, 4060)
        state.ingest_event(event(112, "SL ONE MORE TIME TO 4054", minute=2))
        self.assertEqual(state.signals["1:110"].current_sl, 4054)
        self.assertEqual(len(state.signals), 1)
        self.assertEqual(state.signals["1:110"].history[-1]["sl_after"], 4054)

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
