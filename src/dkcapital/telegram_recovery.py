"""Telegram reconnection recovery for DKcapital signal providers.

Reads message IDs + latest edited bodies, not just the live socket.
Old recovered entry messages retain their original Telegram timestamp; the
broker EA's entry-age guard remains authoritative.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def signature(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    return (
        str(payload.get("text") or ""),
        str(payload.get("edit_date")) if payload.get("edit_date") else None,
        str(payload.get("date")) if payload.get("date") else None,
    )


def load_known(path: Path) -> dict[tuple[int, int], tuple[str, str | None, str | None]]:
    known: dict[tuple[int, int], tuple[str, str | None, str | None]] = {}
    if not path.exists():
        return known
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
                if item.get("event_type") == "message_deleted":
                    chat = int(item["chat_id"])
                    for mid in item.get("message_ids") or []:
                        known.pop((chat, int(mid)), None)
                elif item.get("event_type") in (
                    "new_message", "historical_message", "message_edited",
                ):
                    key = (int(item["chat_id"]), int(item["message_id"]))
                    known[key] = signature(item)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
    return known


def decide_event(payload: dict[str, Any],
                 known: dict[tuple[int, int], tuple[str, str | None, str | None]]) -> str | None:
    key = (int(payload["chat_id"]), int(payload["message_id"]))
    previous = known.get(key)
    if previous is None:
        return "historical_message"
    if previous != signature(payload):
        return "message_edited"
    return None


def within_lookback(value: str | None, *, now: datetime, days: int) -> bool:
    if not value:
        return False
    try:
        original = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (original.tzinfo is not None
                and original.astimezone(UTC) >= now - timedelta(days=days))
    except (TypeError, ValueError):
        return False


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(path)
