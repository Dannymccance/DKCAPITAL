"""Regression tests for a corrected GWS BUY stop-loss signal."""
import unittest
from dkcapital.signal_state import SignalState

CHAT = -1003765237177
ROOT = ("BUY XAUUSD AT 4152.50 - 4149.50\n"
        "TP1 4157\nTP2 4159.50\nTP3 4176\nSL 4244")
FIXED = ROOT.replace("SL 4244", "SL 4144")


def msg(mid, body, when, kind="new_message", chat=CHAT, reply=None):
    return {
        "event_type": kind, "chat_id": chat, "message_id": mid,
        "date": when, "text": body, "reply_to_message_id": reply,
    }


class GwsCorrectionTests(unittest.TestCase):
    def test_root_edit_corrects_stop_without_resetting_age(self):
        state = SignalState()
        initial = msg(7050, ROOT, "2026-10-15T17:57:46+00:00")
        state.ingest_event(initial)
        updated = dict(initial, event_type="message_edited", text=FIXED,
                       edit_date="2026-10-15T17:59:37+00:00")
        state.ingest_event(updated)
        signal = state.signals[f"{CHAT}:7050"]
        self.assertEqual(signal.current_sl, 4144)
        self.assertEqual(signal.opened_at, "2026-10-15T17:57:46+00:00")
        self.assertEqual(signal.source_message_ids, [7050])
        self.assertEqual(len(state.signals), 1)

    def test_standalone_sl_targets_only_wrong_side_stop(self):
        state = SignalState()
        state.ingest_event(msg(
            7049, "BUY XAUUSD AT 4155 - 4158\nSL 4147",
            "2026-10-15T17:56:40+00:00",
        ))
        state.ingest_event(msg(7050, ROOT, "2026-10-15T17:57:46+00:00"))
        state.ingest_event(msg(7051, "SL 4144 ofcource*",
                               "2026-10-15T18:00:00+00:00"))
        self.assertEqual(state.signals[f"{CHAT}:7050"].current_sl, 4144)
        self.assertEqual(state.signals[f"{CHAT}:7049"].current_sl, 4147)
        self.assertEqual(state.message_targets[(CHAT, 7051)], [f"{CHAT}:7050"])

    def test_ambiguous_invalid_stops_are_quarantined(self):
        state = SignalState()
        state.ingest_event(msg(7050, ROOT, "2026-10-15T17:57:46+00:00"))
        state.ingest_event(msg(
            7052, ROOT.replace("4152.50 - 4149.50", "4153 - 4150"),
            "2026-10-15T17:58:00+00:00",
        ))
        state.ingest_event(msg(
            7053, "SL 4144 ofcource*", "2026-10-15T18:00:00+00:00",
        ))
        self.assertEqual(state.signals[f"{CHAT}:7050"].current_sl, 4244)
        self.assertEqual(state.signals[f"{CHAT}:7052"].current_sl, 4244)
        self.assertTrue(any(
            a["reason"] == "unresolved_bare_sl"
            for a in state.unresolved_actions
        ))

    def test_cross_provider_correction_does_not_apply(self):
        state = SignalState()
        state.ingest_event(msg(7050, ROOT, "2026-10-15T17:57:46+00:00"))
        state.ingest_event(msg(
            1, "SL 4144 ofcource*", "2026-10-15T18:00:00+00:00", chat=2,
        ))
        self.assertEqual(state.signals[f"{CHAT}:7050"].current_sl, 4244)

    def test_explicit_both_sl_retains_group_semantics(self):
        state = SignalState()
        state.ingest_event(msg(
            7050, ROOT.replace("SL 4244", "SL 4145"),
            "2026-10-15T17:57:46+00:00",
        ))
        state.ingest_event(msg(
            7052, ROOT.replace("4152.50 - 4149.50", "4148 - 4146")
                      .replace("SL 4244", "SL 4140"),
            "2026-10-15T17:58:00+00:00",
        ))
        state.ingest_event(msg(
            7053, "BOTH SL TO 4138", "2026-10-15T18:00:00+00:00",
        ))
        self.assertEqual(state.signals[f"{CHAT}:7050"].current_sl, 4138)
        self.assertEqual(state.signals[f"{CHAT}:7052"].current_sl, 4138)


if __name__ == "__main__":
    unittest.main()
