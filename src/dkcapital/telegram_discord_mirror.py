"""Mirror raw Telegram source messages to a private Discord channel.

This is read-only presentation; no trading commands originate from Discord.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import discord

logger = logging.getLogger("dkcapital.telegram_mirror")

# Existing private DK Capital Discord #chats channel. Preserve any deliberate
# DISCORD_TELEGRAM_CHATS_CHANNEL_ID override, including 0 to disable mirroring.
DEFAULT_CHATS_CHANNEL_ID = 1559140564253806593


def chats_channel_id() -> int:
    """Choose the already established #chats destination by default."""
    configured = os.getenv("DISCORD_TELEGRAM_CHATS_CHANNEL_ID", "").strip()
    return int(configured) if configured else DEFAULT_CHATS_CHANNEL_ID


def format_message(event: dict) -> str:
    """Concise fallback content, retained for existing consumers."""
    return str(event.get("text") or "[Empty Telegram message]")


def message_embed(event: dict) -> discord.Embed:
    """A legible, timestamped source-message view without invented attribution."""
    from datetime import datetime

    source = str(event.get("chat_title") or f"Telegram {event.get('chat_id')}")
    author = str(event.get("sender_name") or
                 (f"Telegram user {event['sender_id']}" if event.get("sender_id") else "Channel post (author not provided)"))
    raw = str(event.get("text") or "")
    if not raw:
        raw = "[Media attached]" if event.get("has_media") else "[Empty message]"
    # A Telegram text message's 4096-character limit fits in one Discord embed.
    embed = discord.Embed(
        title=source[:256],
        description=raw[:4096],
        color=discord.Color.blue(),
    )
    embed.add_field(name="Author", value=author[:1024], inline=True)
    stamp = event.get("date")
    if stamp:
        try:
            timestamp = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if timestamp.tzinfo:
                embed.timestamp = timestamp
                embed.add_field(name="Sent", value=f"<t:{int(timestamp.timestamp())}:F>", inline=True)
        except (TypeError, ValueError):
            embed.add_field(name="Sent", value=str(stamp)[:1024], inline=True)
    if event.get("edit_date"):
        try:
            edited = datetime.fromisoformat(str(event["edit_date"]).replace("Z", "+00:00"))
            embed.add_field(name="Edited", value=f"<t:{int(edited.timestamp())}:F>", inline=False)
        except (TypeError, ValueError):
            embed.add_field(name="Edited", value=str(event["edit_date"])[:1024], inline=False)
    if event.get("reply_to_message_id"):
        embed.add_field(name="Reply to Telegram message", value=str(event["reply_to_message_id"]), inline=True)
    if event.get("has_media"):
        embed.add_field(name="Attachment", value=f"{event.get('media_type') or 'Telegram media'} (file not mirrored yet)", inline=False)
    embed.set_footer(text=f"Telegram chat {event.get('chat_id')} | message {event.get('message_id')}")
    return embed

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
                        embed = message_embed(event)
                        if original:
                            try:
                                posted = await channel.fetch_message(int(original))
                                await posted.edit(content=None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
                            except discord.NotFound:
                                posted = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                                messages[key] = posted.id
                        else:
                            posted = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                            messages[key] = posted.id
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Telegram mirror delivery failed; event retained for retry")
                break
            state["offset"] = stream.tell()
            state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = state_path.with_suffix(".tmp")
            temp.write_text(json.dumps(state), encoding="utf-8")
            temp.replace(state_path)
