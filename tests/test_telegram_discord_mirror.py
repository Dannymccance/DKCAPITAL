import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from datetime import UTC, datetime

from dkcapital.telegram_discord_mirror import DEFAULT_CHATS_CHANNEL_ID, chats_channel_id, message_embed, sync_mirror


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

    def test_existing_chats_destination_is_default(self):
        with patch.dict("os.environ", {"DISCORD_TELEGRAM_CHATS_CHANNEL_ID": ""}):
            self.assertEqual(chats_channel_id(), 1559140564253806593)
            self.assertEqual(chats_channel_id(), DEFAULT_CHATS_CHANNEL_ID)
        with patch.dict("os.environ", {"DISCORD_TELEGRAM_CHATS_CHANNEL_ID": "0"}):
            self.assertEqual(chats_channel_id(), 0)

    def test_every_monitored_source_goes_to_same_chats_channel(self):
        names = [
            "GWS (VIP)", "GWS - MONEY HOUSE", "GWS - CAPITAL",
            "GWS - STORM", "Elite Portfolios",
        ]
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            state = Path(folder) / "mirror.json"
            log.write_text("".join(json.dumps({
                "event_type": "new_message",
                "chat_id": -1000000000000 - i,
                "message_id": 100 + i,
                "chat_title": name,
                "text": f"Message from {name}",
            }) + "\n" for i, name in enumerate(names)), encoding="utf-8")
            state.write_text(json.dumps({"offset": 0, "messages": {}}), encoding="utf-8")
            channel = Mock()
            channel.send = AsyncMock(side_effect=[Mock(id=501 + i) for i in range(len(names))])
            bot = Mock()
            bot.get_channel.return_value = channel
            asyncio.run(sync_mirror(
                bot, channel_id=DEFAULT_CHATS_CHANNEL_ID,
                event_log=log, state_path=state,
            ))
            bot.get_channel.assert_called_once_with(DEFAULT_CHATS_CHANNEL_ID)
            self.assertEqual(channel.send.await_count, len(names))
            observed = [call.kwargs["embed"].title for call in channel.send.await_args_list]
            self.assertEqual(observed, names)
            self.assertEqual(len(json.loads(state.read_text())["messages"]), len(names))
            self.assertEqual(json.loads(state.read_text())["offset"], log.stat().st_size)

    def test_source_edits_and_deletes_update_existing_discord_message(self):
        chat_id = -10087654
        new = {"event_type": "new_message", "chat_id": chat_id,
               "message_id": 12, "chat_title": "GWS - STORM", "text": "BUY XAUUSD"}
        edit = {**new, "event_type": "message_edited", "text": "BUY XAUUSD SL 4200"}
        delete = {"event_type": "message_deleted", "chat_id": chat_id,
                  "message_ids": [12]}
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "events.jsonl"
            state = Path(folder) / "mirror.json"
            log.write_text("".join(json.dumps(event) + "\n" for event in (new, edit, delete)))
            state.write_text(json.dumps({"offset": 0, "messages": {}}))
            posted = Mock()
            posted.id = 777
            posted.edit = AsyncMock()
            posted.delete = AsyncMock()
            channel = Mock()
            channel.send = AsyncMock(return_value=posted)
            channel.fetch_message = AsyncMock(return_value=posted)
            bot = Mock()
            bot.get_channel.return_value = channel
            asyncio.run(sync_mirror(
                bot, channel_id=DEFAULT_CHATS_CHANNEL_ID,
                event_log=log, state_path=state,
            ))
            channel.send.assert_awaited_once()
            posted.edit.assert_awaited_once()
            self.assertEqual(posted.edit.await_args.kwargs["embed"].description,
                             "BUY XAUUSD SL 4200")
            posted.delete.assert_awaited_once()
            self.assertEqual(json.loads(state.read_text())["messages"], {})


if __name__ == "__main__":
    unittest.main()
