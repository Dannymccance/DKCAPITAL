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


def normalize_telemetry(payload: Any, expected_login: int, *,
                        expected_mode: str = "DEMO",
                        expected_currency: str = "GBP",
                        expected_server: str | None = None) -> dict[str, Any]:
    """Validate broker-originated telemetry against an exact MT5 account."""
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("invalid telemetry version")
    if expected_mode not in ("DEMO", "REAL") or expected_currency not in ("GBP", "USD"):
        raise ValueError("invalid expected profile")
    if str(payload.get("mode")) != expected_mode:
        raise ValueError("unexpected broker mode")
    if str(payload.get("currency")) != expected_currency:
        raise ValueError("unexpected broker currency")
    if expected_server is not None and payload.get("server") != expected_server:
        raise ValueError("unexpected broker server")
    login = _integer(payload.get("login"), minimum=1)
    if login != expected_login:
        raise ValueError("unexpected account")
    generated = _datetime(payload.get("generated_at"))
    if abs((datetime.now(UTC) - generated).total_seconds()) > 120:
        raise ValueError("stale or future-dated telemetry")
    if payload.get("risk_schedule") is not None:
        if payload.get("risk_schedule") not in {"UK_V2", "TP1_V1"} or "credit" not in payload:
            raise ValueError("invalid live UK session risk telemetry")
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
        if not 5100000000 <= magic <= 48100000000 or leg not in (1, 2, 3, 4, 5, 6, 7):
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
            "risk_account_currency": (
                _number(p["risk_account_currency"], minimum=0)
                if p.get("risk_account_currency") is not None else None
            ),
        })
    # Newer EAs report account-wide exposure. Legacy telemetry is accepted
    # for reporting, but is insufficient to authorize automated MT5 recovery.
    broker_fields = (
        "broker_positions_total", "broker_orders_total",
        "unmanaged_positions_count", "broker_positions_protected",
    )
    broker_exposure = {}
    present = [field in payload for field in broker_fields]
    if any(present):
        if not all(present):
            raise ValueError("incomplete broker exposure telemetry")
        for field in broker_fields[:3]:
            broker_exposure[field] = _integer(payload[field])
        protected = payload["broker_positions_protected"]
        if not isinstance(protected, bool):
            raise ValueError("invalid broker position protection flag")
        broker_exposure["broker_positions_protected"] = protected
        count = broker_exposure["broker_positions_total"]
        unmanaged = broker_exposure["unmanaged_positions_count"]
        if count != unmanaged + len(out_pos):
            raise ValueError("broker exposure counts do not reconcile")
        if count == 0 and not protected:
            raise ValueError("empty broker account cannot be unprotected")

    # Pending-order inventory is optional for older EAs, but mandatory for
    # unattended recovery whenever broker_orders_total is non-zero.
    order_fields = ("orders", "unmanaged_orders_count")
    if any(k in payload for k in order_fields):
        if not all(k in payload for k in order_fields) or not all(present):
            raise ValueError("incomplete pending order telemetry")
        orders = payload["orders"]
        if not isinstance(orders, list) or len(orders) > 200:
            raise ValueError("invalid pending order list")
        unmanaged_orders = _integer(payload["unmanaged_orders_count"])
        out_orders = []
        seen_orders = set()
        for order in orders:
            if not isinstance(order, dict):
                raise ValueError("invalid pending order")
            ticket = _integer(order["ticket"], minimum=1)
            if ticket in seen_orders:
                raise ValueError("duplicate pending order ticket")
            seen_orders.add(ticket)
            magic = _integer(order["magic"])
            if not (5100000001 <= magic <= 48049672957 and
                    magic % 10 in (1, 2, 3, 4, 5, 6, 7)):
                raise ValueError("unowned pending order")
            if order.get("symbol") != "XAUUSD":
                raise ValueError("unexpected pending order symbol")
            order_type = order.get("type")
            if order_type not in ("BUY_LIMIT", "BUY_STOP", "SELL_LIMIT", "SELL_STOP"):
                raise ValueError("unsupported pending order type")
            lots = _number(order["lots"], minimum=1e-6)
            price = _number(order["price"], minimum=1e-6)
            sl = _number(order["sl"], minimum=0)
            tp = _number(order["tp"], minimum=0)
            out_orders.append({
                "ticket": ticket, "magic": magic, "symbol": "XAUUSD",
                "type": order_type, "lots": lots, "price": price,
                "sl": sl, "tp": tp,
            })
        if len(out_orders) + unmanaged_orders != broker_exposure["broker_orders_total"]:
            raise ValueError("broker pending order counts do not reconcile")
        broker_exposure["unmanaged_orders_count"] = unmanaged_orders
        broker_exposure["orders"] = out_orders

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
        if not 5100000000 <= magic <= 48100000000 or magic % 10 not in (1, 2, 3, 4, 5, 6, 7):
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
            "reason": str(d.get("reason") or "UNVERIFIED").upper()
                if str(d.get("reason") or "UNVERIFIED").upper() in
                {"TP", "SL", "CLIENT", "MOBILE", "WEB", "EXPERT", "SO", "UNVERIFIED"}
                else "UNVERIFIED",
        })
    out_deals.sort(key=lambda d: (d["time"], d["ticket"]))
    diagnostics = payload.get("decisions", [])
    if not isinstance(diagnostics, list) or len(diagnostics) > 160:
        raise ValueError("invalid MT5 decision diagnostics")
    decision_rows = []
    for item in diagnostics:
        if not isinstance(item, dict):
            raise ValueError("invalid MT5 decision")
        signal_id = str(item.get("signal_id") or "")
        stage = str(item.get("stage") or "").upper()
        code = str(item.get("code") or "").upper()
        if (not __import__("re").fullmatch(r"[-0-9:]{1,64}", signal_id)
                or stage not in {"ENTRY", "LAYER", "PROTECTION", "STOP"}
                or not __import__("re").fullmatch(r"[A-Z0-9_]{1,64}", code)):
            raise ValueError("invalid MT5 decision identifier")
        stamp = _datetime(item.get("at"))
        if abs((generated - stamp).total_seconds()) > 86400:
            raise ValueError("MT5 decision outside one-day retention window")
        decision_rows.append({
            "signal_id": signal_id, "stage": stage, "code": code,
            "prior_code": str(item.get("prior_code") or "").upper()[:64],
            "at": stamp.isoformat(), "bid": _number(item.get("bid", 0), minimum=0),
            "ask": _number(item.get("ask", 0), minimum=0),
            "age_seconds": _integer(item.get("age_seconds", 0)),
            "broker_retcode": _integer(item.get("broker_retcode", 0)),
        })
    return {
        "version": 2, "mode": expected_mode, "login": login,
        "currency": expected_currency, "server": payload.get("server"),
        "generated_at": generated.isoformat(),
        **numeric, **broker_exposure, "positions": out_pos, "deals": out_deals,
        "feed_http": _integer(payload.get("feed_http", 0)),
        "entry_enabled": payload.get("entry_enabled") is True,
        "terminal_trade_allowed": (payload.get("terminal_trade_allowed") if type(payload.get("terminal_trade_allowed")) is bool else None),
        "mql_trade_allowed": (payload.get("mql_trade_allowed") if type(payload.get("mql_trade_allowed")) is bool else None),
        "layer_entries_enabled": payload.get("layer_entries_enabled") is True,
        "daily_loss_warning": payload.get("daily_loss_warning") is True,
        "daily_loss_guard_mode": (
            "ALERT_ONLY" if payload.get("daily_loss_guard_mode") == "ALERT_ONLY"
            else "LEGACY_OR_UNKNOWN"
        ),
        "daily_loss_day": _integer(payload.get("daily_loss_day", 0)),
        "daily_loss_warning_pct": _number(payload.get("daily_loss_warning_pct", 2.0), minimum=0),
        "signal_risk_cap_pct": (_number(payload["signal_risk_cap_pct"], minimum=0)
                                if "signal_risk_cap_pct" in payload else None),
        "signal_risk_cap_gbp": (_number(payload["signal_risk_cap_gbp"], minimum=0)
                                if "signal_risk_cap_gbp" in payload else None),
        "credit": _number(payload.get("credit", 0), minimum=0),
        "risk_schedule": (payload.get("risk_schedule") if payload.get("risk_schedule") in {"UK_V2", "TP1_V1"} else None),
        "portfolio_risk_cap_account_currency": (
            _number(payload["portfolio_risk_cap_account_currency"], minimum=0)
            if "portfolio_risk_cap_account_currency" in payload else None
        ),
        "signal_risk_cap_account_currency": (
            _number(payload["signal_risk_cap_account_currency"], minimum=0)
            if "signal_risk_cap_account_currency" in payload else None
        ),
        "decisions": decision_rows,
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
    # Monetary P&L weights each closed price movement by its actual lots.
    # Keep the legacy summed signal-pips metric separately: it intentionally
    # weights each signal equally and may have a different sign.
    closed_moves = [
        (lots, pips)
        for exits in group_exits.values()
        for lots, pips, _ in exits
    ]
    closed_lots = sum(lots for lots, _ in closed_moves)
    week_lot_weighted_pips = (
        sum(lots * pips for lots, pips in closed_moves) / closed_lots
        if closed_lots else 0.0
    )
    positions = []
    groups: dict[int, float] = defaultdict(float)
    risk_unknown = False
    for p in snapshot["positions"]:
        direction = 1 if p["side"] == "BUY" else -1
        market = snapshot["bid"] if direction == 1 else snapshot["ask"]
        marked_pips = (market - p["entry"]) * direction / pip_size
        risk = (p.get("risk_account_currency") if snapshot["currency"] == "USD"
                else p.get("risk_gbp"))
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
        "floating_gbp": round(snapshot["equity"] - snapshot["balance"]
                              - (float(snapshot.get("credit") or 0)
                                 if snapshot.get("mode") == "REAL" else 0), 2),
        "today_realized_gbp": round(today_cash, 2),
        "week_realized_gbp": round(weekly_cash, 2),
        "week_realized_pips": round(weekly_pips, 1),
        "week_lot_weighted_pips": round(week_lot_weighted_pips, 1),
        "week_closed_legs": weekly_closes, "week_closed_signals": len(group_exits), "pip_size": pip_size,
        "positions": positions,
        "feed_http": snapshot.get("feed_http", 0),
        "entry_enabled": snapshot.get("entry_enabled", False),
        "risk_unknown": risk_unknown,
        "portfolio_risk_gbp": None if risk_unknown else round(sum(groups.values()), 2),
        "group_risks": {str(k): round(v, 2) for k, v in groups.items()},
    }
