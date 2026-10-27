"""Live TP1 account entry policy. Gateway prefilter; broker EA remains final authority.

The approved Telegram peer IDs are pinned to observed channel IDs, not mutable
titles. This module never turns an entry into an order or infers broker fills.
"""
from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

STORM_CHAT_ID = -1004334637102
CAPITAL_CHAT_ID = -1004406990046
APPROVED_SOURCES = {STORM_CHAT_ID: "GWS STORM", CAPITAL_CHAT_ID: "GWS Capital"}
RISK_SCHEDULE = "TP1_V1"
RISK_PCT = 8.0
ENTRY_AGE_LIMIT_SECONDS = 180  # Safety freshness limit, NOT an entry delay.


def uk_entry_window(now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    hour = now.astimezone(ZoneInfo("Europe/Isle_of_Man")).hour
    return 0 <= hour < 15


def attested_tp1_risk(snapshot: dict[str, Any]) -> bool:
    """Reject a legacy EA, forged risk cap or credit-funded risk budget."""
    try:
        if (snapshot.get("risk_schedule") != RISK_SCHEDULE or
                snapshot.get("mode") != "REAL" or snapshot.get("currency") != "USD"):
            return False
        balance, equity, credit = (
            float(snapshot[k]) for k in ("balance", "equity", "credit")
        )
        per_signal = float(snapshot["signal_risk_cap_pct"])
        limit = float(snapshot["portfolio_risk_cap_account_currency"])
        capital = min(balance, max(0.0, equity - credit))
        return (
            all(isfinite(x) for x in (balance, equity, credit, per_signal, limit))
            and balance > 0 and equity > 0 and credit >= 0 and capital > 0
            and abs(per_signal - RISK_PCT) < 1e-6
            and abs(limit - capital * RISK_PCT / 100.0) <= 0.15
        )
    except (TypeError, KeyError, ValueError, OverflowError):
        return False


def source_id(signal: dict[str, Any]) -> int | None:
    try:
        ident = signal.get("origin_chat_id")
        if ident is None:
            # Only genuine old listener records (no forwarding metadata)
            # can safely fall back to the destination peer. New forwarded
            # records carry an explicit 0 to fail closed.
            ident = signal.get("chat_id")
        return int(ident)
    except (TypeError, ValueError):
        return None


def provider_label(signal: dict[str, Any]) -> str:
    ident = source_id(signal)
    if ident in APPROVED_SOURCES:
        return APPROVED_SOURCES[ident]
    return {"gws": "GWS", "elite": "Elite Portfolios"}.get(
        str(signal.get("source_style") or "").lower(), "Telegram provider"
    )


def _float(value: Any) -> float:
    result = float(value)
    if not isfinite(result):
        raise ValueError("non-finite price")
    return result


def tp1_block_reason(signal: dict[str, Any], snapshot: dict[str, Any],
                     *, now: datetime | None = None,
                     max_deviation_usd: float = 1.0) -> str | None:
    """Pre-screen NOW entry; never remove old feed rows or management actions."""
    now = now or datetime.now(UTC)
    if source_id(signal) not in APPROVED_SOURCES or signal.get("source_style") != "gws":
        return "PROVIDER_DISABLED"
    if signal.get("order_kind") == "LIMIT":
        return "LIMIT_ORDER_NOT_CONVERTED"
    if signal.get("order_kind") != "MARKET_NOW":
        return "NOT_AN_APPROVED_NOW_ENTRY"
    if not uk_entry_window(now):
        return "UK_TRADING_SESSION_CLOSED"
    stamp = signal.get("entry_ready_at")
    if not stamp:
        return "WAITING_FOR_COMPLETE_PROVIDER_INSTRUCTIONS"
    try:
        ready = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if ready.tzinfo is None or not -15 <= (now-ready).total_seconds() <= ENTRY_AGE_LIMIT_SECONDS:
            return "ENTRY_WINDOW_EXPIRED"
        direction = str(signal.get("direction") or "")
        if direction not in ("BUY", "SELL"):
            return "INVALID_DIRECTION"
        bid = _float(snapshot["bid"])
        ask = _float(snapshot["ask"])
        if bid <= 0 or ask < bid:
            return "BROKER_QUOTE_INVALID"
        fill = ask if direction == "BUY" else bid
        sl = _float(signal.get("current_sl") or 0)
        if sl <= 0:
            return "PROVIDER_SL_MISSING"
        if (direction == "BUY" and sl >= bid) or (direction == "SELL" and sl <= ask):
            return "PROVIDER_SL_INVALID"
        tps = signal.get("tps") or {}
        tp1 = _float(tps.get("1", tps.get(1, 0)))
        if tp1 <= 0 or (direction == "BUY" and tp1 <= ask) or (direction == "SELL" and tp1 >= bid):
            return "TP1_ALREADY_PASSED_OR_INVALID"
        if max_deviation_usd < 0 or not isfinite(max_deviation_usd):
            return "INVALID_ENTRY_DEVIATION_CONFIG"
        low = _float(signal.get("entry_low") or 0)
        high = _float(signal.get("entry_high") or 0)
        if low > 0 and high > 0 and not (min(low, high)-max_deviation_usd <= fill <= max(low, high)+max_deviation_usd):
            return "ENTRY_DEVIATION_EXCEEDED"
        if int(snapshot.get("unmanaged_positions_count") or 0):
            return "UNMANAGED_BROKER_EXPOSURE"
        if int(snapshot.get("broker_orders_total") or 0):
            return "PENDING_BROKER_ORDERS"
        # Strict no-hedge rule, including same-provider opposite directions.
        if any(p.get("side") != direction for p in snapshot.get("positions", [])):
            return "OPPOSING_POSITION_ACTIVE"
        # The broker EA uses OrderCalcProfit and OrderCalcMargin for the
        # definitive lot size, stop exposure, slippage and spread decision.
        return None
    except (ValueError, TypeError, KeyError, OverflowError):
        return "SIGNAL_PRICE_OR_BROKER_DATA_INVALID"
