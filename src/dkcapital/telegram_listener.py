from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from unicodedata import normalize
from typing import Any

from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession

from dkcapital.config import ChatRef, Settings
from dkcapital.event_store import JsonlEventStore
from dkcapital.logging_setup import configure_logging
from dkcapital.market_data import GoldSpotClient
from dkcapital.telegram_recovery import (
    decide_event, load_known, signature, within_lookback, write_status,
)

logger = logging.getLogger("dkcapital.telegram")

# Only these explicitly requested GWS channels may be discovered by title.
# Existing TELEGRAM_SOURCE_CHATS identities remain pinned by ID/username.
AUTO_GWS_TITLES = frozenset({
    "gws - money house",
    "gws - capital",
    "gws - storm",
    "gws (vip)",
    "gws - vip",
    "gws vip",
    "gws-vip",
})


def _discover_auto_gws_chats(dialogs: list[Any]) -> dict[int, str]:
    """Resolve exact GWS titles in joined Telegram channels, failing closed on duplicates.

    Telegram titles are mutable and are not authentication. Operators should
    ultimately pin verified IDs in TELEGRAM_SOURCE_CHATS for live execution.
    """
    matched: dict[str, dict[int, str]] = {}
    for dialog in dialogs:
        entity = dialog.entity
        # Ignore personal messages and ordinary groups. Only channel-style
        # dialogs joined by the authenticated user can be auto-discovered.
        if not (getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False)):
            continue
        title = str(getattr(entity, "title", None) or dialog.name or "")
        # Telegram channel titles may use mathematical bold Unicode glyphs
        # (e.g. "𝐆𝐖𝐒 (𝐕𝐈𝐏)"). NFKC folds these into ordinary letters.
        normalised = " ".join(normalize("NFKC", title).split()).casefold()
        if normalised not in AUTO_GWS_TITLES:
            continue
        peer_id = utils.get_peer_id(entity)
        matched.setdefault(normalised, {})[peer_id] = title

    discovered: dict[int, str] = {}
    for title, candidates in matched.items():
        if len(candidates) != 1:
            logger.error(
                "Ambiguous auto-discovered Telegram title %r, IDs=%s: "
                "source not activated; pin verified chat ID to TELEGRAM_SOURCE_CHATS",
                title, sorted(candidates),
            )
            continue
        discovered.update(candidates)
    return discovered



def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _reply_to_message_id(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    return getattr(reply_to, "reply_to_msg_id", None) if reply_to else None


def _is_gold_message(text: str) -> bool:
    return bool(re.search(r"\b(?:GOLD|XAU(?:USD)?)\b", text, re.IGNORECASE))


def _message_payload(event_type: str, message: Any, *, chat_title: str | None = None, sender_name: str | None = None) -> dict[str, Any]:
    media = getattr(message, "media", None)
    forwarded = getattr(message, "fwd_from", None)
    forwarded_peer = getattr(forwarded, "from_id", None) if forwarded else None
    try:
        origin = utils.get_peer_id(forwarded_peer) if forwarded_peer else None
    except (TypeError, ValueError):
        origin = None

    return {
        "event_type": event_type,
        "observed_at": datetime.now(UTC).isoformat(),
        "chat_id": getattr(message, "chat_id", None),
        # Forwarded posts are never trusted solely by the destination chat.
        "is_forwarded": forwarded is not None,
        "forwarded_from_chat_id": origin,
        "message_id": getattr(message, "id", None),
        "sender_id": getattr(message, "sender_id", None),
        "sender_name": sender_name,
        "chat_title": chat_title,
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
    gold_spot = GoldSpotClient(
        settings.gold_spot_url,
        settings.gold_spot_refresh_seconds,
    )

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
    configured_chat_ids = tuple(chat_ids)
    known = load_known(settings.telegram_event_log_path)
    dialogs = await client.get_dialogs()
    chat_titles = {
        utils.get_peer_id(d.entity): str(d.name or getattr(d.entity, "title", None) or "Telegram")
        for d in dialogs
    }
    sender_names: dict[int, str] = {}

    def refresh_sources(current_dialogs: list[Any]) -> None:
        """Refresh the allowlist in place so all live handlers see new joins."""
        discovered = (
            _discover_auto_gws_chats(current_dialogs)
            if settings.telegram_auto_gws_sources else {}
        )
        updated = list(dict.fromkeys((*configured_chat_ids, *discovered.keys())))
        added = set(updated) - set(chat_ids)
        removed = set(chat_ids) - set(updated)
        chat_ids[:] = updated
        for chat_id, title in discovered.items():
            chat_titles[chat_id] = title
        if added or removed:
            logger.warning(
                "Telegram source membership changed added=%s removed=%s; "
                "auto-discovered titles must be ID-verified before trusting live orders",
                sorted(added), sorted(removed),
            )

    refresh_sources(dialogs)

    async def detailed_payload(kind: str, message: Any) -> dict[str, Any]:
        sender = getattr(message, "sender", None)
        sender_id = getattr(message, "sender_id", None)
        if sender is None and sender_id is not None and sender_id not in sender_names:
            try:
                sender = await message.get_sender()
            except Exception:
                logger.debug("Unable to resolve Telegram sender id=%s", sender_id)
        name = None
        if sender is not None:
            name = utils.get_display_name(sender) or getattr(sender, "title", None)
            if name and sender_id is not None:
                sender_names[sender_id] = name
        if not name:
            name = sender_names.get(sender_id)
        chat_id = getattr(message, "chat_id", None)
        return _message_payload(
            kind, message,
            chat_title=chat_titles.get(chat_id),
            sender_name=name or (chat_titles.get(chat_id) if sender_id is None else None),
        )

    store_lock = asyncio.Lock()
    recovery_status_path = settings.telegram_event_log_path.parent / "telegram-recovery-status.json"
    lookback_days = 7

    async def record(payload: dict[str, Any]) -> bool:
        """Record instructions immediately, avoiding slow external quote requests."""
        async with store_lock:
            kind = payload.get("event_type")
            if kind == "message_deleted":
                for message_id in payload.get("message_ids") or []:
                    known.pop((int(payload["chat_id"]), int(message_id)), None)
                store.append(payload)
                return True
            if kind not in ("new_message", "message_edited", "historical_message"):
                return False
            observed = decide_event(payload, known)
            if observed is None:
                return False
            if kind == "historical_message":
                payload["event_type"] = observed
            elif observed == "message_edited":
                payload["event_type"] = "message_edited"
            else:
                payload["event_type"] = "new_message"
            quote = gold_spot.cached_quote()
            if quote is not None and _is_gold_message(str(payload.get("text") or "")):
                payload["market"] = quote
            store.append(payload)
            key = (int(payload["chat_id"]), int(payload["message_id"]))
            known[key] = signature(payload)
            # Never log the raw Telegram signal text in INFO logs.
            logger.info("telegram_event chat=%s id=%s kind=%s",
                        key[0], key[1], payload["event_type"])
            return True

    async def on_new_message(event: events.NewMessage.Event) -> None:
        if event.chat_id not in chat_ids:
            return
        await record(await detailed_payload("new_message", event.message))

    async def on_message_edited(event: events.MessageEdited.Event) -> None:
        if event.chat_id not in chat_ids:
            return
        await record(await detailed_payload("message_edited", event.message))

    async def on_message_deleted(event: events.MessageDeleted.Event) -> None:
        if event.chat_id not in chat_ids:
            return
        payload = {
            "event_type": "message_deleted",
            "observed_at": datetime.now(UTC).isoformat(),
            "chat_id": event.chat_id,
            "message_ids": list(event.deleted_ids),
        }
        await record(payload)

    # Builder-level chats= filters are frozen at registration. Filter inside
    # callbacks instead so membership changes are picked up without a restart.
    client.add_event_handler(on_new_message, events.NewMessage())
    client.add_event_handler(on_message_edited, events.MessageEdited())
    client.add_event_handler(on_message_deleted, events.MessageDeleted())
    logger.info("Telegram listener handlers ready sources=%s log=%s",
                chat_ids, settings.telegram_event_log_path)

    async def refresh_gold_quote() -> None:
        while True:
            try:
                await gold_spot.quote()
            except Exception:
                logger.exception("Gold quote background refresh failed")
            await asyncio.sleep(15)

    async def reconcile_recent(limit: int) -> dict[str, Any]:
        """Catch missed new messages and edits, preserving original Telegram dates.

        This is bounded to protect Telegram API quotas. Saturation is recorded
        as degraded readiness rather than silently claiming complete recovery.
        """
        now = datetime.now(UTC)
        dialogs = await client.get_dialogs()
        refresh_sources(dialogs)
        entities = {utils.get_peer_id(item.entity): item.entity for item in dialogs}
        recovered = 0
        amended = 0
        scans: dict[str, Any] = {}
        for chat_id in chat_ids:
            entity = entities.get(chat_id)
            if entity is None:
                raise RuntimeError(f"configured source not visible: {chat_id}")
            messages = []
            async for item in client.iter_messages(entity, limit=limit):
                created = _iso(getattr(item, "date", None))
                if not within_lookback(created, now=now, days=lookback_days):
                    break
                messages.append(item)
            for item in reversed(messages):
                payload = await detailed_payload("historical_message", item)
                old = decide_event(payload, known)
                if old is not None and await record(payload):
                    recovered += old == "historical_message"
                    amended += old == "message_edited"
            # Reaching the scan limit means there could be older edited/missed
            # messages outside the bounded recovery window.
            at_limit = (len(messages) >= limit and
                        (int(chat_id), int(messages[-1].id)) not in known)
            scans[str(chat_id)] = {
                "recent_checked": len(messages),
                "scan_limited": at_limit,
            }
        return {
            "healthy": not any(x["scan_limited"] for x in scans.values()),
            "checked_at": datetime.now(UTC).isoformat(),
            "recovered": recovered,
            "amended": amended,
            "sources": scans,
        }

    async def recovery_loop() -> None:
        scan_number = 0
        while True:
            try:
                # A deeper periodic scan catches old-message SL edits that
                # would not appear in the most recent 120 provider posts.
                deep_scan = (scan_number % 30 == 0)
                result = await reconcile_recent(800 if deep_scan else 120)
                write_status(recovery_status_path, result)
                logger.info("Telegram recovery health=%s recovered=%s amended=%s sources=%s",
                            result["healthy"], result["recovered"],
                            result["amended"], result["sources"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Telegram recovery scan failed")
                write_status(recovery_status_path, {
                    "healthy": False,
                    "checked_at": datetime.now(UTC).isoformat(),
                    "error": type(exc).__name__,
                })
            scan_number += 1
            await asyncio.sleep(60)

    gold_task = asyncio.create_task(refresh_gold_quote())
    recovery_task = asyncio.create_task(recovery_loop())
    try:
        await client.run_until_disconnected()
    finally:
        for task in (gold_task, recovery_task):
            task.cancel()
        await asyncio.gather(gold_task, recovery_task, return_exceptions=True)
        await client.disconnect()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
