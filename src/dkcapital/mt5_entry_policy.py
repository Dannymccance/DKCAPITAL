"""Account-scoped new-entry gate for opposing Telegram chat positions.

Use only broker-confirmed, fresh, identity-verified position snapshots.
Returning a reason suppresses *new* EA entries using the existing CANCELLED
feed status; the original S row, stop updates and A/L actions remain visible.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group


def verified_uk_session_risk(snapshot: dict[str, Any], *,
                             now: datetime | None = None) -> bool:
    """Require EA-attested UK 10%/4% broker risk, not a gateway-config guess.

    A prior 4% V2.11 binary must remain management-only after migration.
    Risk caps come from the EA's actual broker balance, equity and credit.
    """
    from math import isfinite
    from zoneinfo import ZoneInfo

    now = now or datetime.now(UTC)
    hour = now.astimezone(ZoneInfo("Europe/Isle_of_Man")).hour
    pct = 10.0 if 1 <= hour < 9 else 4.0
    try:
        if snapshot.get("risk_schedule") != "UK_V2":
            return False
        if snapshot.get("mode") != "REAL" or snapshot.get("currency") != "USD":
            return False
        actual_pct = float(snapshot["signal_risk_cap_pct"])
        balance = float(snapshot["balance"])
        equity = float(snapshot["equity"])
        credit = float(snapshot["credit"])
        reported_cap = float(snapshot["portfolio_risk_cap_account_currency"])
        values = (actual_pct, balance, equity, credit, reported_cap)
        if (not all(isfinite(value) for value in values)
                or balance <= 0 or equity <= 0 or credit < 0
                or abs(actual_pct - pct) > 1e-6):
            return False
        expected = min(balance, max(0.0, equity - credit)) * pct / 100.0
        return expected > 0 and abs(reported_cap - expected) <= 0.15
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


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
    # This is specifically a cross-Telegram-room policy. Unmanaged manual
    # positions have no provider-room identity and must not be invented as one.
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


def recent_entry_blocks(
    signals: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    verified: bool,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """Report current blocked opportunities without rewriting provider status."""
    now = now or datetime.now(UTC)
    blocked: list[dict[str, str]] = []
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("status") != "ACTIVE":
            continue
        try:
            opened = datetime.fromisoformat(str(signal["opened_at"]).replace("Z", "+00:00"))
            if opened.tzinfo is None or not -15 <= (now - opened).total_seconds() <= 300:
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        reason = ("BROKER_TELEMETRY_UNVERIFIED" if not verified else
                  new_entry_block_reason(signal, signals, snapshot))
        if reason:
            blocked.append({"signal_id": str(signal.get("signal_id") or ""),
                            "reason": reason})
    return blocked
