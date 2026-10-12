"""Broker-authenticated Account 1 reporting; no execution capability.

MT5 deal history is the sole authority for realised financial/pip performance.
All amounts in the account currency (GBP for Account 1).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Isle_of_Man")
EXPECTED_PIP_SIZE = 0.01  # provisional; confirm actual broker/provider convention


def _number(value: Any, *, minimum: float = -1e14) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric")
    result = float(value)
    if not isfinite(result) or result < minimum:
        raise ValueError("invalid numeric value")
    return result


def _integer(value: Any, *, minimum: int = 0) -> int:
    result = int(value)
    if result < minimum:
        raise ValueError("invalid integer")
    return result


def _datetime(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC)


def normalize_telemetry(payload: Any, expected_login: int) -> dict[str, Any]:
    """Reject non-demo, wrong-account, non-GBP and malformed broker reports."""
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("invalid telemetry version")
    if str(payload.get("mode")) != "DEMO":
        raise ValueError("demo account required")
    if str(payload.get("currency")) != "GBP":
        raise ValueError("GBP account required")
    login = _integer(payload.get("login"), minimum=1)
    if login != expected_login:
        raise ValueError("unexpected account")
    generated = _datetime(payload.get("generated_at"))
    if abs((datetime.now(UTC) - generated).total_seconds()) > 120:
        raise ValueError("stale or future-dated telemetry")
    fields = ("balance", "equity", "margin", "free_margin", "bid", "ask")
    numeric = {field: _number(payload[field], minimum=0) for field in fields}
    if numeric["bid"] <= 0 or numeric["ask"] < numeric["bid"]:
        raise ValueError("invalid quote")
    positions = payload.get("positions")
    deals = payload.get("deals")
    if not isinstance(positions, list) or len(positions) > 200:
        raise ValueError("invalid positions")
    if not isinstance(deals, list) or len(deals) > 1500:
        raise ValueError("invalid deals")
    out_pos = []
    tickets = set()
    for p in positions:
        if not isinstance(p, dict) or p.get("side") not in ("BUY", "SELL"):
            raise ValueError("invalid position")
        ticket = _integer(p["ticket"], minimum=1)
        if ticket in tickets:
            raise ValueError("duplicate ticket")
        tickets.add(ticket)
        magic = _integer(p["magic"])
        leg = magic % 10
        if not 5100000000 <= magic <= 48100000000 or leg not in (1, 2, 3):
            raise ValueError("unowned position")
        out_pos.append({
            "ticket": ticket, "magic": magic, "group": magic // 10,
            "leg": leg, "side": p["side"],
            "lots": _number(p["lots"], minimum=1e-6),
            "entry": _number(p["entry"], minimum=1e-6),
            "sl": _number(p["sl"], minimum=0),
            "tp": _number(p["tp"], minimum=0),
            "profit": _number(p["profit"]), "swap": _number(p["swap"]),
            "risk_gbp": _number(p["risk_gbp"], minimum=0) if p.get("risk_gbp") is not None else None,
        })
    out_deals = []
    seen_deals = set()
    for d in deals:
        if not isinstance(d, dict) or d.get("entry_type") not in ("IN", "OUT"):
            raise ValueError("invalid deal")
        deal_ticket = _integer(d["ticket"], minimum=1)
        if deal_ticket in seen_deals:
            raise ValueError("duplicate deal")
        seen_deals.add(deal_ticket)
        magic = _integer(d["magic"])
        if not 5100000000 <= magic <= 48100000000 or magic % 10 not in (1, 2, 3):
            raise ValueError("unowned deal")
        out_deals.append({
            "ticket": deal_ticket,
            "position_id": _integer(d["position_id"], minimum=1),
            "magic": magic, "group": magic // 10,
            "entry_type": d["entry_type"],
            "side": str(d.get("side") or ""),
            "lots": _number(d["lots"], minimum=1e-6),
            "price": _number(d["price"], minimum=1e-6),
            "profit": _number(d["profit"]),
            "commission": _number(d["commission"]),
            "swap": _number(d["swap"]),
            "time": _datetime(d["time"]).isoformat(),
        })
    out_deals.sort(key=lambda d: (d["time"], d["ticket"]))
    return {
        "version": 2, "mode": "DEMO", "login": login,
        "currency": "GBP", "generated_at": generated.isoformat(),
        **numeric, "positions": out_pos, "deals": out_deals,
        "feed_http": _integer(payload.get("feed_http", 0)),
        "entry_enabled": payload.get("entry_enabled") is True,
        "received_at": datetime.now(UTC).isoformat(),
    }


def summarize(snapshot: dict[str, Any], *, pip_size: float = EXPECTED_PIP_SIZE,
              now: datetime | None = None) -> dict[str, Any]:
    if pip_size <= 0 or not isfinite(pip_size):
        raise ValueError("invalid pip size")
    now = now or datetime.now(UTC)
    now = now.astimezone(UTC)
    local = now.astimezone(TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = (midnight - timedelta(days=midnight.weekday())).astimezone(UTC)
    today_start = midnight.astimezone(UTC)
    by_position: dict[int, list[dict[str, Any]]] = defaultdict(list)
    group_total_lots: dict[int, float] = defaultdict(float)
    for d in snapshot["deals"]:
        by_position[d["position_id"]].append(d)
        if d["entry_type"] == "IN":
            group_total_lots[d["group"]] += d["lots"]
    today_cash = weekly_cash = 0.0
    weekly_closes = 0
    # Treat the three MT5 legs as one provider signal, not three trades.
    group_exits: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    for position_deals in by_position.values():
        entry_deals = [d for d in position_deals if d["entry_type"] == "IN"]
        opened_volume = sum(d["lots"] for d in entry_deals)
        if opened_volume <= 0:
            continue
        entry_price = sum(d["lots"] * d["price"] for d in entry_deals) / opened_volume
        entry_side = entry_deals[0]["side"]
        if entry_side not in ("BUY", "SELL"):
            continue
        direction = 1 if entry_side == "BUY" else -1
        for d in position_deals:
            ts = _datetime(d["time"])
            # Opening commissions also count in the period they were actually charged.
            cash = d["profit"] + d["commission"] + d["swap"]
            if ts >= week_start:
                weekly_cash += cash
            if ts >= today_start:
                today_cash += cash
            if d["entry_type"] != "OUT" or ts < week_start:
                continue
            group_exits[d["group"]].append((
                d["lots"], (d["price"] - entry_price) * direction / pip_size,
                entry_price,
            ))
            weekly_closes += 1
    # Each signal contributes one volume-averaged price move, independent of
    # how many TP legs or partial-close deals MT5 produced.
    weekly_pips = sum(
        sum(lots * pips for lots, pips, _ in exits) /
        sum(lots for lots, _, _ in exits)
        for exits in group_exits.values() if sum(lots for lots, _, _ in exits) > 0
    )
    positions = []
    groups: dict[int, float] = defaultdict(float)
    risk_unknown = False
    for p in snapshot["positions"]:
        direction = 1 if p["side"] == "BUY" else -1
        market = snapshot["bid"] if direction == 1 else snapshot["ask"]
        marked_pips = (market - p["entry"]) * direction / pip_size
        risk = p["risk_gbp"]
        if risk is None or p["sl"] <= 0:
            risk_unknown = True
        else:
            groups[p["group"]] += risk
        positions.append({**p, "floating_pips": round(marked_pips, 1)})
    received = _datetime(snapshot["received_at"])
    return {
        "login": snapshot["login"], "generated_at": snapshot["generated_at"],
        "received_at": snapshot["received_at"], "stale": (now - received).total_seconds() > 120,
        "balance": snapshot["balance"], "equity": snapshot["equity"],
        "free_margin": snapshot["free_margin"], "margin": snapshot["margin"],
        "floating_gbp": round(snapshot["equity"] - snapshot["balance"], 2),
        "today_realized_gbp": round(today_cash, 2),
        "week_realized_gbp": round(weekly_cash, 2),
        "week_realized_pips": round(weekly_pips, 1),
        "week_closed_legs": weekly_closes, "week_closed_signals": len(group_exits), "pip_size": pip_size,
        "positions": positions,
        "feed_http": snapshot.get("feed_http", 0),
        "entry_enabled": snapshot.get("entry_enabled", False),
        "risk_unknown": risk_unknown,
        "portfolio_risk_gbp": None if risk_unknown else round(sum(groups.values()), 2),
        "group_risks": {str(k): round(v, 2) for k, v in groups.items()},
    }
