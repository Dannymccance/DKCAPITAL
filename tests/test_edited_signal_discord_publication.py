"""Regression coverage: a bare Telegram direction edited into an MT5 signal."""
from __future__ import annotations

import unittest

from dkcapital.discord_bot import _new_signal_card_eligible
from dkcapital.signal_parser import parse_actions
from dkcapital.signal_state import SignalState


CHAT = -1004334637102
MESSAGE = 8742
ORIGINAL = "2026-10-26T10:00:00+00:00"
EDITED = "2026-10-26T10:05:00+00:00"
CUTOVER = "2026-10-26T10:03:00+00:00"
FULL = "XAU USD SELL NOW 4082-86\nTP 4080\nSL 4087"


def provider_post(text: str, *, kind: str = "new_message",
                  observed: str = ORIGINAL,
                  edited: str | None = None) -> dict:
    return {
        "event_type": kind,
        "chat_id": CHAT,
        "message_id": MESSAGE,
        "date": ORIGINAL,
        "observed_at": observed,
        "edit_date": edited,
        "text": text,
    }


class EditedSignalPublishingTests(unittest.TestCase):
    def test_entry_added_by_edit_becomes_publishable_without_changing_ea_time(self):
        self.assertFalse(any(
            action.kind == "new_signal"
            for action in parse_actions("XAU USD SELL NOW")
        ))
        state = SignalState()
        state.ingest_event(provider_post("XAU USD SELL NOW"))
        self.assertEqual(state.snapshot()["signals"], [])

        state.ingest_event(provider_post(
            FULL, kind="message_edited", observed=EDITED, edited=EDITED
        ))
        signals = state.snapshot()["signals"]
        self.assertEqual(len(signals), 1)
        signal = signals[0]
        self.assertEqual(signal["signal_id"], f"{CHAT}:{MESSAGE}")
        self.assertEqual(signal["opened_at"], ORIGINAL)
        self.assertEqual(signal["publish_ready_at"], EDITED)
        self.assertEqual(signal["direction"], "SELL")
        self.assertEqual((signal["entry_low"], signal["entry_high"]), (4082, 4086))
        self.assertEqual(signal["tps"]["1"], 4080)
        self.assertTrue(_new_signal_card_eligible(signal, CUTOVER))
        self.assertFalse(_new_signal_card_eligible(signal, "2026-10-26T10:06:00Z"))

        # Restart reconstruction from the full event log must give the same
        # timestamps, even though rebuild keeps only the latest message body.
        recovered = SignalState()
        for payload in (provider_post("XAU USD SELL NOW"), provider_post(
            FULL, kind="message_edited", observed=EDITED, edited=EDITED
        )):
            recovered.ingest_event(payload, rebuild=False)
        recovered.rebuild()
        self.assertEqual(recovered.snapshot()["signals"][0]["publish_ready_at"], EDITED)
        self.assertEqual(recovered.snapshot()["signals"][0]["opened_at"], ORIGINAL)

    def test_later_edit_does_not_advance_first_valid_publication_time(self):
        state = SignalState()
        state.ingest_event(provider_post("XAU USD SELL NOW"))
        state.ingest_event(provider_post(
            FULL, kind="message_edited", observed=EDITED, edited=EDITED
        ))
        state.ingest_event(provider_post(
            "XAU USD SELL NOW 4082-86\nTP 4079\nSL 4087",
            kind="message_edited",
            observed="2026-10-26T10:07:00+00:00",
            edited="2026-10-26T10:07:00+00:00",
        ))
        signal = state.snapshot()["signals"][0]
        self.assertEqual(signal["publish_ready_at"], EDITED)
        self.assertEqual(signal["tps"]["1"], 4079)
        self.assertFalse(_new_signal_card_eligible(
            signal, "2026-10-26T10:06:00+00:00"
        ))

    def test_existing_valid_message_edit_does_not_replay_old_signals(self):
        state = SignalState()
        state.ingest_event(provider_post(FULL))
        state.ingest_event(provider_post(
            "XAU USD SELL NOW 4082-86\nTP 4079\nSL 4087",
            kind="message_edited", observed=EDITED, edited=EDITED
        ))
        signal = state.snapshot()["signals"][0]
        self.assertEqual(signal["publish_ready_at"], ORIGINAL)
        self.assertFalse(_new_signal_card_eligible(signal, CUTOVER))

    def test_historical_recovered_signal_keeps_original_cutover_time(self):
        state = SignalState()
        state.ingest_event(provider_post(
            FULL, kind="historical_message", observed=EDITED, edited=EDITED
        ))
        signal = state.snapshot()["signals"][0]
        self.assertEqual(signal["publish_ready_at"], ORIGINAL)
        self.assertFalse(_new_signal_card_eligible(signal, CUTOVER))

    def test_missing_edit_date_uses_observed_time(self):
        state = SignalState()
        state.ingest_event(provider_post("XAU USD SELL NOW"))
        state.ingest_event(provider_post(
            FULL, kind="message_edited", observed=EDITED
        ))
        self.assertTrue(_new_signal_card_eligible(
            state.snapshot()["signals"][0], CUTOVER
        ))

    def test_unseen_edited_message_uses_edit_date_without_replaying_old_posts(self):
        state = SignalState()
        # A Telegram edit arrives without a previously captured initial post.
        state.ingest_event(provider_post(
            FULL, kind="new_message", observed=EDITED, edited=EDITED
        ))
        self.assertEqual(state.snapshot()["signals"][0]["publish_ready_at"], EDITED)
        self.assertTrue(_new_signal_card_eligible(
            state.snapshot()["signals"][0], CUTOVER
        ))

    def test_legacy_state_falls_back_to_opened_at_and_normalizes_offsets(self):
        legacy = {"opened_at": ORIGINAL}
        self.assertFalse(_new_signal_card_eligible(legacy, CUTOVER))
        current = {"opened_at": ORIGINAL, "publish_ready_at": "2026-10-26T11:05:00+01:00"}
        self.assertTrue(_new_signal_card_eligible(current, CUTOVER))
        self.assertFalse(_new_signal_card_eligible(current, "invalid"))
        self.assertFalse(_new_signal_card_eligible({}, CUTOVER))


if __name__ == "__main__":
    unittest.main()
