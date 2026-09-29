from __future__ import annotations

import unittest

from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState


def event(
    message_id: int,
    text: str,
    *,
    minute: int,
    chat_id: int = 200,
) -> dict:
    return {
        "event_type": "new_message",
        "chat_id": chat_id,
        "message_id": message_id,
        "date": f"2026-09-28T09:{minute:02d}:00+00:00",
        "reply_to_message_id": None,
        "text": text,
    }


GOLD_4281 = """I am personally entering

Gold buy lower lot half lots

high risk

Buy I've gone lower lot high risk

Entry: 4281 low lot

Stop loss: 4275

Take profit: Open

This is not financial advice

DISCLAIMER"""


class EliteParserTests(unittest.TestCase):
    def test_structured_gold_signal(self) -> None:
        action = parse_actions(GOLD_4281)[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.source_style, "elite")
        self.assertEqual(action.symbol, "XAUUSD")
        self.assertEqual(action.direction, "BUY")
        self.assertEqual(action.entry_low, 4281)
        self.assertEqual(action.entry_high, 4281)
        self.assertEqual(action.sl, 4275)
        self.assertEqual(action.tps, {})

    def test_structured_btc_signal(self) -> None:
        text = """I am personally entering

Sell

Btc Get ready smaller now

Sell higher risk smaller lot

Entry: 81700

Stop loss: 82370

Take profit: Open

This is not financial advice"""
        action = parse_actions(text)[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.source_style, "elite")
        self.assertEqual(action.symbol, "BTCUSDT")
        self.assertEqual(action.direction, "SELL")
        self.assertEqual(action.entry_low, 81700)
        self.assertEqual(action.sl, 82370)

    def test_structured_gold_signal_without_space_after_entry_price(self) -> None:
        text = """I am personally entering

Gold  sell  lower lot  half lots

high risk

Sell I’ve gone lower lot high risk

Entry: 4157low lot

Stop loss: 4164

Take profit:   Open

This is not financial advice

DISCLAIMER"""
        action = parse_actions(text)[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.source_style, "elite")
        self.assertEqual(action.symbol, "XAUUSD")
        self.assertEqual(action.direction, "SELL")
        self.assertEqual(action.entry_low, 4157)
        self.assertEqual(action.entry_high, 4157)
        self.assertEqual(action.sl, 4164)

    def test_elite_labelled_price_separator_variants(self) -> None:
        variants = (
            ("Entry 4157", "Stop loss 4164"),
            ("Entry - 4157", "Stop loss - 4164"),
            ("Entry @ 4157", "SL @ 4164"),
            ("Entry: 4,157low lot", "SL: 4,164"),
        )
        for entry_line, sl_line in variants:
            with self.subTest(entry_line=entry_line, sl_line=sl_line):
                text = f"""I am personally entering

Gold sell lower lot

Sell

{entry_line}

{sl_line}

Take profit: Open"""
                action = parse_actions(text)[0]
                self.assertEqual(action.kind, "new_signal")
                self.assertEqual(action.entry_low, 4157)
                self.assertEqual(action.sl, 4164)

    def test_elite_signal_can_open_before_sl_arrives(self) -> None:
        text = """I am personally entering

Gold buy lower lot

Buy

Entry: 4157low lot

Take profit: Open"""
        action = parse_actions(text)[0]
        self.assertEqual(action.kind, "new_signal")
        self.assertEqual(action.entry_low, 4157)
        self.assertIsNone(action.sl)

    def test_malformed_elite_new_signal_cannot_update_older_trade(self) -> None:
        state = SignalState()
        state.ingest_event(event(30, GOLD_4281, minute=0))

        malformed = """I am personally entering

Gold sell lower lot

Sell

Entry: pending

Stop loss: 4164

Take profit: Open"""
        state.ingest_event(event(31, malformed, minute=5))

        existing = state.signals["200:30"]
        self.assertEqual(existing.current_sl, 4275)
        self.assertNotIn("200:31", state.signals)

        unresolved = [
            item
            for item in state.unresolved_actions
            if item["message_id"] == 31
        ]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(
            unresolved[0]["action"]["kind"],
            "unparsed_new_signal",
        )

    def test_partial_and_stop_update_are_two_actions(self) -> None:
        actions = parse_actions(
            "Being cautious Take 50% partial on gold now and set stop loss to this 4281"
        )
        partial = next(action for action in actions if action.kind == "partial_close")
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(partial.symbol, "XAUUSD")
        self.assertEqual(partial.partial_percent, 50)
        self.assertEqual(partial.scope, "latest_symbol")
        self.assertEqual(update.sl, 4281)
        self.assertEqual(update.scope, "latest_symbol")

    def test_further_50_percent_is_of_remaining_position(self) -> None:
        state = SignalState()
        state.ingest_event(event(1, GOLD_4281, minute=0))
        state.ingest_event(
            event(
                2,
                "Being cautious Take 50% partial on gold now and set stop loss to this 4281",
                minute=2,
            )
        )
        state.ingest_event(
            event(
                3,
                "Now heavy partials\nBeing cautious Take FURTHER 50% partial on gold now "
                "and set stop loss to this 4281",
                minute=4,
            )
        )
        signal = state.signals["200:1"]
        self.assertEqual(signal.partial_close_count, 2)
        self.assertAlmostEqual(signal.remaining_fraction, 0.25)
        self.assertEqual(signal.current_sl, 4281)

    def test_elite_same_direction_signals_are_independent(self) -> None:
        state = SignalState()
        state.ingest_event(
            event(
                10,
                GOLD_4281.replace("4281", "4263").replace("4275", "4257"),
                minute=0,
            )
        )
        state.ingest_event(
            event(
                11,
                GOLD_4281.replace("4281", "4261").replace("4275", "4255"),
                minute=10,
            )
        )
        first = state.signals["200:10"]
        second = state.signals["200:11"]
        self.assertNotEqual(first.group_id, second.group_id)

        state.ingest_event(
            event(
                12,
                "Being cautious Take 50% partial on gold now and set stop loss to this 4261",
                minute=17,
            )
        )
        first = state.signals["200:10"]
        second = state.signals["200:11"]
        self.assertEqual(first.partial_close_count, 0)
        self.assertEqual(first.current_sl, 4257)
        self.assertEqual(second.partial_close_count, 1)
        self.assertEqual(second.current_sl, 4261)

    def test_tp_half_and_sl_example(self) -> None:
        actions = parse_actions("Gold TP half and set SL to 4292")
        partial = next(action for action in actions if action.kind == "partial_close")
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(partial.partial_percent, 50)
        self.assertEqual(update.sl, 4292)
        self.assertEqual(update.symbol, "XAUUSD")

    def test_trailing_stop_language(self) -> None:
        actions = parse_actions("Trail your stop loss to 4292 to guarantee 200 pips")
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.sl, 4292)

        actions = parse_actions("Sl to 4282 guaranteed 300 pips")
        update = next(action for action in actions if action.kind == "trade_update")
        self.assertEqual(update.sl, 4282)

    def test_btc_partial_targets_latest_btc_trade(self) -> None:
        state = SignalState()
        btc = """I am personally entering

Buy

Btc Get ready smaller now

Buy higher risk smaller lot

Entry: 82900

Stop loss: 82270

Take profit: Open"""
        state.ingest_event(event(20, btc, minute=0))
        state.ingest_event(
            event(
                21,
                "Being cautious Take 50% partial on Btc now and set stop loss to this 82900",
                minute=5,
            )
        )
        signal = state.signals["200:20"]
        self.assertEqual(signal.symbol, "BTCUSDT")
        self.assertEqual(signal.partial_close_count, 1)
        self.assertAlmostEqual(signal.remaining_fraction, 0.5)
        self.assertEqual(signal.current_sl, 82900)

    def test_pip_brag_without_instruction_is_commentary(self) -> None:
        self.assertEqual(parse_actions("330 pips boom")[0].kind, "commentary")
        self.assertEqual(parse_actions("🔥🔥🔥620pips🔥🔥🔥")[0].kind, "commentary")


if __name__ == "__main__":
    unittest.main()
