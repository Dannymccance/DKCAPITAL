from __future__ import annotations

import asyncio

from telethon import TelegramClient, utils
from telethon.sessions import StringSession

from dkcapital.config import Settings


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_telegram_credentials()

    if not settings.telegram_session_string:
        raise RuntimeError(
            "TELEGRAM_SESSION_STRING is missing. Generate it with the telegram-session helper first."
        )

    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.connect()

    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("The Telegram session is not authorized.")

    print("CHAT_ID\tTITLE\tUSERNAME")

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        chat_id = utils.get_peer_id(entity)
        title = dialog.name or ""
        username = getattr(entity, "username", None)
        username_display = f"@{username}" if username else ""
        print(f"{chat_id}\t{title}\t{username_display}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
