import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from datetime import UTC, datetime

from dkcapital.telegram_discord_mirror import message_embed, sync_mirror


class TelegramMirrorPresentationTests(unittest.TestCase):
    def test_source_author_times_reply_and_full_text(self):
        body = "Signal content\n" * 200
        event = {
            "chat_id": -1002, "chat_title": "GWS VIP", "sender_id": 42,
            "sender_name": "Trader John", "message_id": 1001,
            "date": "2026-10-12T11:10:00+00:00",
            "edit_date": "2026-10-12T11:12:00+00:00",
            "reply_to_message_id": 999,
            "text": body,
        }
        embed = message_embed(event)
        self.assertEqual(embed.title, "GWS VIP")
        self.assertEqual(embed.description, body)
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["Author"], "Trader John")
        self.assertIn("<t:", fields["Sent"])
        self.assertIn("<t:", fields["Edited"])
        self.assertEqual(fields["Reply to Telegram message"], "999")

    def test_new_message_delivery_does_not_reference_undefined_body(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            state = Path(folder) / "state.json"
            event = {
                "event_type": "new_message", "chat_id": -1002,
                "message_id": 1001, "text": "BUY XAUUSD", "chat_title": "GWS",
            }
            log.write_text(json.dumps(event) + "\n", encoding="utf-8")
            state.write_text(json.dumps({"offset": 0, "messages": {}}))
            channel = Mock()
            channel.send = AsyncMock(return_value=Mock(id=123))
            bot = Mock()
            bot.get_channel.return_value = channel
            asyncio.run(sync_mirror(bot, channel_id=987,
                                    event_log=log, state_path=state))
            channel.send.assert_awaited_once()
            self.assertEqual(channel.send.await_args.kwargs["embed"].description,
                             "BUY XAUUSD")
            self.assertEqual(json.loads(state.read_text())["messages"]["-1002:1001"], 123)

    def test_missing_sender_is_not_misattributed(self):
        embed = message_embed({"chat_id": -1002, "message_id": 1, "text": "Hi"})
        self.assertIn("author not provided", embed.fields[0].value)


if __name__ == "__main__":
    unittest.main()
