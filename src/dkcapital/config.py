from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from dotenv import load_dotenv

load_dotenv()

ChatRef: TypeAlias = int | str


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _chat_ref(value: str) -> ChatRef:
    try:
        return int(value)
    except ValueError:
        return value


@dataclass(frozen=True)
class Settings:
    app_env: str
    log_level: str

    telegram_api_id: int | None
    telegram_api_hash: str | None
    telegram_session_string: str | None
    telegram_source_chats: tuple[ChatRef, ...]
    telegram_event_log_path: Path

    discord_bot_token: str | None
    discord_guild_id: int | None

    @classmethod
    def from_env(cls) -> "Settings":
        api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
        guild_id_raw = os.getenv("DISCORD_GUILD_ID", "").strip()

        return cls(
            app_env=os.getenv("APP_ENV", "production").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
            telegram_api_id=int(api_id_raw) if api_id_raw else None,
            telegram_api_hash=os.getenv("TELEGRAM_API_HASH", "").strip() or None,
            telegram_session_string=os.getenv("TELEGRAM_SESSION_STRING", "").strip() or None,
            telegram_source_chats=tuple(_chat_ref(v) for v in _csv("TELEGRAM_SOURCE_CHATS")),
            telegram_event_log_path=Path(
                os.getenv("TELEGRAM_EVENT_LOG_PATH", "/app/data/telegram-events.jsonl")
            ),
            discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip() or None,
            discord_guild_id=int(guild_id_raw) if guild_id_raw else None,
        )

    def validate_telegram_credentials(self) -> None:
        missing: list[str] = []
        if self.telegram_api_id is None:
            missing.append("TELEGRAM_API_ID")
        if not self.telegram_api_hash:
            missing.append("TELEGRAM_API_HASH")

        if missing:
            raise RuntimeError(
                "Missing required Telegram environment variables: " + ", ".join(missing)
            )

    def validate_telegram_listener(self) -> None:
        self.validate_telegram_credentials()

        missing: list[str] = []
        if not self.telegram_session_string:
            missing.append("TELEGRAM_SESSION_STRING")
        if not self.telegram_source_chats:
            missing.append("TELEGRAM_SOURCE_CHATS")

        if missing:
            raise RuntimeError(
                "Missing required Telegram listener environment variables: "
                + ", ".join(missing)
            )

    def validate_discord(self) -> None:
        if not self.discord_bot_token:
            raise RuntimeError("Missing required environment variable: DISCORD_BOT_TOKEN")
