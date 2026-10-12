"""Mirror raw Telegram source messages to a private Discord channel.

This is read-only presentation; no trading commands originate from Discord.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import discord

logger = logging.getLogger("dkcapital.telegram_mirror")


def format_message(event: dict) -> str:
    source = str(event.get("chat_id") or "unknown")
    message = str(event.get("message_id") or "unknown")
    text = str(event.get("text") or "")
    media = event.get("media_type")
    if not text:
        text = "[Media attachment]" if event.get("has_media") else "[Empty message]"
    if media:
        text += f"\n[Telegram media: {media}; not copied]"
    heading = f"**Telegram {source} | message {message}**"
    if event.get("reply_to_message_id"):
        heading += f" | reply to {event['reply_to_message_id']}"
    return (heading + "\n" + text)[:2000]


async def sync_mirror(bot: discord.Client, *, channel_id: int,
                      event_log: Path, state_path: Path) -> None:
    if not channel_id or not event_log.exists():
        return
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.exception("Telegram mirror state unreadable; refusing unsafe replay")
            return
    else:
        # Start with new events only. Historical provider messages aren't dumped.
        state = {"offset": event_log.stat().st_size, "messages": {}}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return
    offset = int(state.get("offset", 0))
    if offset > event_log.stat().st_size:
        logger.error("Telegram event log shrank; manual mirror reconciliation needed")
        return
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    messages = state.setdefault("messages", {})
    with event_log.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break  # wait until JSONL append completes
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                logger.exception("Malformed Telegram event; mirror halted")
                break
            kind = event.get("event_type")
            chat_id = event.get("chat_id")
            ids = event.get("message_ids", []) if kind == "message_deleted" else [event.get("message_id")]
            try:
                for message_id in ids:
                    if chat_id is None or message_id is None:
                        continue
                    key = f"{chat_id}:{message_id}"
                    original = messages.get(key)
                    if kind == "message_deleted":
                        if original:
                            try:
                                posted = await channel.fetch_message(int(original))
                                await posted.delete()
                            except discord.NotFound:
                                pass
                            messages.pop(key, None)
                    elif kind in ("new_message", "message_edited"):
                        body = format_message(event)
                        if original:
                            try:
                                posted = await channel.fetch_message(int(original))
                                await posted.edit(content=body, allowed_mentions=discord.AllowedMentions.none())
                            except discord.NotFound:
                                posted = await channel.send(body, allowed_mentions=discord.AllowedMentions.none())
                                messages[key] = posted.id
                        else:
                            posted = await channel.send(body, allowed_mentions=discord.AllowedMentions.none())
                            messages[key] = posted.id
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Telegram mirror delivery failed; event retained for retry")
                break
            state["offset"] = stream.tell()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state), encoding="utf-8")
            temp.replace(state_path)
