from __future__ import annotations

import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from dkcapital.config import Settings


async def run() -> None:
    settings = Settings.from_env()
    settings.validate_telegram_credentials()

    print("Generating a Telegram StringSession.")
    print("Telegram may ask for your phone number, login code, and 2FA password.")
    print("The resulting session string is sensitive. Store it only in your .env file.")
    print()

    client = TelegramClient(
        StringSession(),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.start()
    session = client.session.save()

    print()
    print("TELEGRAM_SESSION_STRING=" + session)
    print()
    print("Copy that complete line into .env, then stop this container.")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
