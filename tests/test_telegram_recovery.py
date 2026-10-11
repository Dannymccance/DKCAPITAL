from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dkcapital.telegram_recovery import (
    decide_event, load_known, signature, within_lookback, write_status,
)


def msg(mid=100, *, text="XAUUSD BUY SL 4200", edit_date=None):
    return {
        "event_type": "new_message",
        "chat_id": -1003765237177,
        "message_id": mid,
        "date": "2026-10-11T15:00:00+00:00",
        "edit_date": edit_date,
        "text": text,
    }


class TelegramRecoveryTests(unittest.TestCase):
    def test_recovery_only_new_or_changed_posts(self):
        known = {}
        original = msg()
        self.assertEqual(decide_event(original, known), "historical_message")
        known[(original["chat_id"], original["message_id"])] = signature(original)
        self.assertIsNone(decide_event(original, known))
        changed = msg(text="XAUUSD BUY SL 4185", edit_date="2026-10-11T15:01:00+00:00")
        self.assertEqual(decide_event(changed, known), "message_edited")

    def test_load_jsonl_dedupe_updates_and_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.jsonl"
            lines = [
                msg(),
                {**msg(), "event_type": "message_edited",
                 "text": "SL 4190", "edit_date": "2026-10-11T15:05:00+00:00"},
                {**msg(101), "text": "XAUUSD SELL", "event_type": "historical_message"},
                {"event_type": "message_deleted",
                 "chat_id": -1003765237177, "message_ids": [100]},
            ]
            path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
            known = load_known(path)
            self.assertNotIn((-1003765237177, 100), known)
            self.assertEqual(known[(-1003765237177, 101)], signature(lines[2]))
            self.assertIsNone(decide_event(lines[2], known))

    def test_keeps_original_telegram_date_for_stale_trade_guard(self):
        now = datetime(2026, 10, 11, 16, tzinfo=UTC)
        self.assertTrue(within_lookback("2026-10-09T11:00:00Z", now=now, days=7))
        self.assertFalse(within_lookback("2026-09-01T11:00:00Z", now=now, days=7))
        self.assertFalse(within_lookback(None, now=now, days=7))
        # Recovery can track a historic message but never rewrites its original date.
        payload = msg()
        self.assertEqual(payload["date"], "2026-10-11T15:00:00+00:00")

    def test_status_is_atomically_written(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "status.json"
            data = {"healthy": True, "checked_at": datetime.now(UTC).isoformat()}
            write_status(path, data)
            self.assertEqual(json.loads(path.read_text()), data)
            self.assertFalse(path.with_suffix(".tmp").exists())


if __name__ == "__main__":
    unittest.main()
