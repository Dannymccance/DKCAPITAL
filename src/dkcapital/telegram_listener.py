from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

from dkcapital.config import ChatRef, Settings
from dkcapital.event_store import JsonlEventStore
from dkcapital.logging_setup import configure_logging

logger = logging.getLogger("dkcapital.telegram")


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _reply_to_message_id(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    return getattr(reply_to, "reply_to_msg_id", None) if reply_to else None


def _message_payload(event_type: str, message: Any) -> dict[str, Any]:
    media = getattr(message, "media", None)

    return {
        "event_type": event_type,
        "observed_at": datetime.now(UTC).isoformat(),
        "chat_id": getattr(message, "chat_id", None),
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


async def _resolve_chat_ids(
    client: TelegramClient,
    configured_chats: tuple[ChatRef, ...],
) -> list[int]:
    # StringSession stores authentication, not a durable entity cache. Loading dialogs
    # here gives Telethon the access hashes needed for private numeric channel IDs.
    dialogs = await client.get_dialogs()
    dialog_entities = {
        utils.get_peer_id(dialog.entity): dialog.entity
        for dialog in dialogs
    }

    resolved: list[int] = []

    for chat in configured_chats:
        try:
            if isinstance(chat, int) and chat in dialog_entities:
                entity = dialog_entities[chat]
            else:
                entity = await client.get_entity(chat)
        except Exception:
            logger.exception(
                "Unable to resolve configured Telegram source %r. "
                "Confirm the listening account can see the chat and that the ID/username is correct.",
                chat,
            )
            raise

        peer_id = utils.get_peer_id(entity)
        if peer_id not in resolved:
            resolved.append(peer_id)

        title = (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or getattr(entity, "first_name", None)
            or "unknown"
        )
        logger.info("Resolved Telegram source %r -> %s (%s)", chat, peer_id, title)

    return resolved


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_telegram_listener()
    configure_logging(settings.log_level)

    store = JsonlEventStore(settings.telegram_event_log_path)

    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "TELEGRAM_SESSION_STRING is not authorized. Generate a new session string."
        )

    me = await client.get_me()
    logger.info(
        "Telegram authenticated user_id=%s username=%s",
        getattr(me, "id", None),
        getattr(me, "username", None),
    )

    chat_ids = await _resolve_chat_ids(client, settings.telegram_source_chats)

    async def record(payload: dict[str, Any]) -> None:
        store.append(payload)
        logger.info("telegram_event %s", json.dumps(payload, ensure_ascii=False))

    async def on_new_message(event: events.NewMessage.Event) -> None:
        await record(_message_payload("new_message", event.message))

    async def on_message_edited(event: events.MessageEdited.Event) -> None:
        await record(_message_payload("message_edited", event.message))

    async def on_message_deleted(event: events.MessageDeleted.Event) -> None:
        payload = {
            "event_type": "message_deleted",
            "observed_at": datetime.now(UTC).isoformat(),
            "chat_id": event.chat_id,
            "message_ids": list(event.deleted_ids),
        }
        await record(payload)

    client.add_event_handler(on_new_message, events.NewMessage(chats=chat_ids))
    client.add_event_handler(on_message_edited, events.MessageEdited(chats=chat_ids))
    client.add_event_handler(on_message_deleted, events.MessageDeleted(chats=chat_ids))

    logger.info(
        "Telegram listener ready sources=%s event_log=%s",
        chat_ids,
        settings.telegram_event_log_path,
    )

    try:
        await client.run_until_disconnected()
    finally:
        await client.disconnect()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
