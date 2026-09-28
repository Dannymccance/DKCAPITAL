from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils
from telethon.sessions import StringSession

from dkcapital.config import ChatRef, Settings


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _reply_to_message_id(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    return getattr(reply_to, "reply_to_msg_id", None) if reply_to else None


def _payload(message: Any, *, source_name: str, source_chat_id: int) -> dict[str, Any]:
    media = getattr(message, "media", None)
    return {
        "event_type": "historical_message",
        "observed_at": datetime.now(UTC).isoformat(),
        "source_name": source_name,
        "chat_id": source_chat_id,
        "message_id": getattr(message, "id", None),
        "sender_id": getattr(message, "sender_id", None),
        "date": _iso(getattr(message, "date", None)),
        "edit_date": _iso(getattr(message, "edit_date", None)),
        "reply_to_message_id": _reply_to_message_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "text": getattr(message, "raw_text", "") or "",
        "has_media": media is not None,
        "media_type": type(media).__name__ if media is not None else None,
    }


async def _resolve_entity(client: TelegramClient, chat: ChatRef):
    dialogs = await client.get_dialogs()
    dialog_entities = {
        utils.get_peer_id(dialog.entity): dialog.entity
        for dialog in dialogs
    }
    if isinstance(chat, int) and chat in dialog_entities:
        return dialog_entities[chat]
    return await client.get_entity(chat)


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Telegram message history for configured DK Capital sources."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of days to look back from now. Default: 30.",
    )
    parser.add_argument(
        "--output",
        default="/app/data/telegram-backfill.jsonl",
        help="JSONL output path.",
    )
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("--days must be at least 1")

    settings = Settings.from_env()
    settings.validate_telegram_listener()

    since = datetime.now(UTC) - timedelta(days=args.days)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("The Telegram session is not authorized.")

    total = 0
    per_source: dict[str, int] = {}

    with output_path.open("w", encoding="utf-8") as handle:
        for configured_chat in settings.telegram_source_chats:
            entity = await _resolve_entity(client, configured_chat)
            source_chat_id = utils.get_peer_id(entity)
            source_name = (
                getattr(entity, "title", None)
                or getattr(entity, "username", None)
                or str(source_chat_id)
            )

            count = 0
            print(
                f"Backfilling {source_name} ({source_chat_id}) "
                f"from {since.isoformat()}..."
            )

            async for message in client.iter_messages(entity):
                message_date = getattr(message, "date", None)
                if message_date is None:
                    continue
                if message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)

                if message_date < since:
                    break

                handle.write(
                    json.dumps(
                        _payload(
                            message,
                            source_name=source_name,
                            source_chat_id=source_chat_id,
                        ),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                count += 1
                total += 1

            per_source[source_name] = count
            print(f"Captured {count} messages from {source_name}.")

    await client.disconnect()

    print()
    print(f"Backfill complete. Total messages: {total}")
    for source_name, count in per_source.items():
        print(f"  {source_name}: {count}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    asyncio.run(run())
