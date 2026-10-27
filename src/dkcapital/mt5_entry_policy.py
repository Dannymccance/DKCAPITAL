"""Account-scoped new-entry gate for opposing Telegram chat positions.

Use only broker-confirmed, fresh, identity-verified position snapshots.
Returning a reason suppresses *new* EA entries using the existing CANCELLED
feed status; the original S row, stop updates and A/L actions remain visible.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group


def signal_chat(signal: dict[str, Any]) -> int | None:
    try:
        chat = int(signal.get("chat_id") or str(signal["signal_id"]).split(":", 1)[0])
        return chat if chat != 0 else None
    except (ValueError, KeyError, TypeError):
        return None


def verified_snapshot(
    snapshot: dict[str, Any],
    *,
    login: int,
    mode: str,
    currency: str,
    server: str | None = None,
    now: datetime | None = None,
) -> bool:
    now = now or datetime.now(UTC)
    try:
        stamp = datetime.fromisoformat(str(snapshot["received_at"]).replace("Z", "+00:00"))
        if (stamp.tzinfo is None or
                not -15 <= (now - stamp).total_seconds() <= 120 or
                snapshot.get("version") != 2 or
                snapshot.get("login") != login or
                snapshot.get("mode") != mode or
                snapshot.get("currency") != currency or
                (server is not None and snapshot.get("server") != server)):
            return False
        positions = snapshot.get("positions")
        return isinstance(positions, list) and all(
            isinstance(p, dict) and
            p.get("side") in ("BUY", "SELL") and
            isinstance(p.get("group"), int)
            for p in positions
        )
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def new_entry_block_reason(
    incoming: dict[str, Any],
    signals: list[dict[str, Any]],
    snapshot: dict[str, Any],
) -> str | None:
    """Reject unknown risk, allow same-side or same-telegram-chat hedges.

    Comparing MT5 magic groups against signal IDs prevents treating different
    GWS Telegram rooms as one source. An unknown opposing group is blocked.
    """
    positions = snapshot.get("positions", [])
    if int(snapshot.get("unmanaged_positions_count") or 0) > 0:
        return "UNMANAGED_BROKER_POSITION"
    if not positions:
        return None
    incoming_side = str(incoming.get("direction") or "")
    incoming_chat = signal_chat(incoming)
    signal_chats = {
        mt5_group(str(s["signal_id"])): signal_chat(s)
        for s in signals
        if isinstance(s, dict) and s.get("signal_id")
    }
    for held in positions:
        if held["side"] == incoming_side:
            continue
        held_chat = signal_chats.get(held["group"])
        if incoming_chat is None or held_chat is None or incoming_chat != held_chat:
            return "OPPOSING_GROUP_POSITION_ACTIVE"
    return None
