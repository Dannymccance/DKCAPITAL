"""Append-only broker-deal archive for the Discord weekly pip average.

The MT5 EA exports a bounded rolling deal window. Retain previously verified
deals by broker ticket so weekly averages do not forget earlier weeks.
This module has no order placement or EA control capability.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
import json
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Isle_of_Man")
HISTORY_VERSION = 1
EA_DEAL_LIMIT = 1000


def _instant(value: str) -> datetime:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None:
        raise ValueError("broker deal without timezone")
    return instant.astimezone(UTC)


def _week_start(instant: datetime):
    day = instant.astimezone(LOCAL).date()
    return day - timedelta(days=day.weekday())


def weekly_average(deals: list[dict[str, Any]], *, now: datetime,
                   pip_size: float = 0.01) -> dict[str, Any] | None:
    """Mean net realised pips over *all* elapsed local calendar weeks.

    A signal contributes a volume-weighted realised price move per week
    in which it had closing deals, matching the existing weekly dashboard.
    Zero-trade weeks count as zero. The current partial week is included.
    """
    if pip_size <= 0 or not isfinite(pip_size):
        raise ValueError("invalid pip size")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not deals:
        return None

    started = min(_instant(d["time"]) for d in deals if d["entry_type"] == "IN") if any(
        d["entry_type"] == "IN" for d in deals
    ) else None
    if started is None:
        return None
    first_week = _week_start(started)
    current_week = _week_start(now)
    if first_week > current_week:
        raise ValueError("broker trades start in the future")

    by_position: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for deal in deals:
        by_position[int(deal["position_id"])].append(deal)

    weekly_exits: dict[Any, dict[int, list[tuple[float, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for position_deals in by_position.values():
        entries = [deal for deal in position_deals if deal["entry_type"] == "IN"]
        if not entries:
            continue
        total_lots = sum(float(deal["lots"]) for deal in entries)
        if total_lots <= 0:
            continue
        entry_price = sum(
            float(deal["lots"]) * float(deal["price"]) for deal in entries
        ) / total_lots
        side = entries[0]["side"]
        if side not in ("BUY", "SELL"):
            continue
        direction = 1 if side == "BUY" else -1

        for deal in position_deals:
            if deal["entry_type"] != "OUT" or _instant(deal["time"]) > now:
                continue
            week = _week_start(_instant(deal["time"]))
            lots = float(deal["lots"])
            if lots <= 0:
                continue
            pips = (float(deal["price"]) - entry_price) * direction / pip_size
            weekly_exits[week][int(deal["group"])].append((lots, pips))

    total = sum(
        sum(
            sum(lots * pips for lots, pips in exits) / sum(lots for lots, _ in exits)
            for exits in groups.values()
            if sum(lots for lots, _ in exits) > 0
        )
        for week, groups in weekly_exits.items() if first_week <= week <= current_week
    )
    weeks = (current_week - first_week).days // 7 + 1
    return {
        "average_pips": round(total / weeks, 1),
        "total_pips": round(total, 1),
        "weeks": weeks,
        "first_trade": started.astimezone(LOCAL).date().isoformat(),
        "closed_signals": len({
            (week, group) for week, groups in weekly_exits.items()
            for group in groups if first_week <= week <= current_week
        }),
    }


def record_weekly_average(path: Path, snapshot: dict[str, Any], *,
                          pip_size: float = 0.01,
                          now: datetime | None = None) -> dict[str, Any] | None:
    """Archive authenticated broker deals, deduping by ticket across restarts.

    Existing ledger corruption, account switches or amended historical tickets
    fail visibly instead of silently resetting lifetime performance.
    """
    if snapshot.get("mode") != "DEMO" or snapshot.get("currency") != "GBP":
        raise ValueError("expected MT5 demo GBP history")
    login = int(snapshot["login"])
    if login <= 0:
        raise ValueError("invalid broker login")
    incoming = snapshot["deals"]
    if not isinstance(incoming, list):
        raise ValueError("invalid broker deals")

    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(stored, dict)
                or stored.get("version") != HISTORY_VERSION
                or stored.get("login") != login
                or stored.get("currency") != "GBP"
                or not isinstance(stored.get("deals"), list)):
            raise ValueError("broker history identity or structure mismatch")
    else:
        stored = {
            "version": HISTORY_VERSION, "login": login,
            "currency": "GBP", "deals": [],
        }

    by_ticket: dict[int, dict[str, Any]] = {}
    for deal in stored["deals"]:
        ticket = int(deal["ticket"])
        if ticket in by_ticket:
            raise ValueError("duplicate archived broker deal")
        by_ticket[ticket] = deal
    changed = not path.exists()
    for deal in incoming:
        ticket = int(deal["ticket"])
        previous = by_ticket.get(ticket)
        if previous is not None and previous != deal:
            raise ValueError(f"broker deal {ticket} changed since archiving")
        if previous is None:
            by_ticket[ticket] = deal
            changed = True

    if changed:
        stored["deals"] = sorted(by_ticket.values(), key=lambda d: (d["time"], d["ticket"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(stored, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    result = weekly_average(list(by_ticket.values()), now=now or datetime.now(UTC),
                            pip_size=pip_size)
    if result is not None:
        result["source_capped"] = len(incoming) >= EA_DEAL_LIMIT
        result["archived_deals"] = len(by_ticket)
    return result
