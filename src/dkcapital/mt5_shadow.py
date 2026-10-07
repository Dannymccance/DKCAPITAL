from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from typing import Any

TERMINAL_SIGNAL_STATUSES = {
    "BREAKEVEN",
    "CANCELLED",
    "CLOSED",
    "COMPLETED",
    "FAILED",
    "STOPPED",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def floor_to_step(value: float, step: float) -> float:
    if value <= 0 or step <= 0:
        return 0.0
    units = math.floor((value + 1e-12) / step)
    stepped = units * step
    return round(stepped, 12)


def lots_for_risk(
    *,
    equity: float,
    risk_pct: float,
    loss_per_lot: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> tuple[float, float]:
    """Return stepped volume and planned risk in account currency.

    loss_per_lot must be the absolute loss returned by MT5 order_calc_profit for
    a one-lot move from the planned entry price to the protective stop.
    The helper never rounds volume upward to satisfy a broker minimum.
    """
    if equity <= 0 or risk_pct <= 0 or loss_per_lot <= 0:
        return 0.0, 0.0
    if volume_min <= 0 or volume_max <= 0 or volume_step <= 0:
        return 0.0, 0.0

    risk_budget = equity * risk_pct
    raw_volume = risk_budget / loss_per_lot
    volume = floor_to_step(raw_volume, volume_step)
    if volume + 1e-12 < volume_min:
        return 0.0, 0.0

    volume = min(volume, volume_max)
    volume = floor_to_step(volume, volume_step)
    if volume + 1e-12 < volume_min:
        return 0.0, 0.0

    return volume, volume * loss_per_lot


def provider_stop_is_structurally_valid(signal: dict[str, Any], stop: float) -> bool:
    low = float(signal.get("entry_low") or 0.0)
    high = float(signal.get("entry_high") or low)
    direction = str(signal.get("direction") or "").upper()
    if direction == "BUY":
        return stop < min(low, high)
    if direction == "SELL":
        return stop > max(low, high)
    return False


def stop_is_actionable(direction: str, stop: float, fill: float) -> bool:
    direction = direction.upper()
    if direction == "BUY":
        return stop < fill
    if direction == "SELL":
        return stop > fill
    return False


def temporary_stop(signal: dict[str, Any], fill: float) -> float:
    low = float(signal.get("entry_low") or fill)
    high = float(signal.get("entry_high") or low)
    lo, hi = min(low, high), max(low, high)
    width = max(0.0, hi - lo)
    buffer = max(6.0, width * 2.0)
    if str(signal.get("direction") or "").upper() == "BUY":
        return min(lo - buffer, fill - buffer)
    return max(hi + buffer, fill + buffer)


def protective_stop_for_signal(signal: dict[str, Any], fill: float) -> tuple[float, str]:
    direction = str(signal.get("direction") or "").upper()
    sl_mode = str(signal.get("sl_mode") or "").upper()
    current_sl = signal.get("current_sl")

    if sl_mode == "BREAKEVEN":
        return fill, "BREAKEVEN"

    if current_sl is not None:
        stop = float(current_sl)
        if provider_stop_is_structurally_valid(signal, stop) and stop_is_actionable(
            direction, stop, fill
        ):
            return stop, "PROVIDER"

    return temporary_stop(signal, fill), "TEMPORARY"


def signal_fingerprint(signal: dict[str, Any]) -> str:
    payload = {
        "signal_id": signal.get("signal_id"),
        "status": signal.get("status"),
        "entry_low": signal.get("entry_low"),
        "entry_high": signal.get("entry_high"),
        "current_sl": signal.get("current_sl"),
        "sl_mode": signal.get("sl_mode"),
        "tps": signal.get("tps") or {},
        "tp_hits": signal.get("tp_hits") or [],
        "remaining_fraction": signal.get("remaining_fraction"),
        "partial_close_count": signal.get("partial_close_count"),
        "layers": signal.get("layers"),
        "reentries": signal.get("reentries"),
        "last_update_at": signal.get("last_update_at"),
        "history_length": len(signal.get("history") or []),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_stale_signal(signal: dict[str, Any], *, now: datetime, max_age_seconds: float) -> bool:
    if max_age_seconds <= 0:
        return False
    opened_at = signal.get("opened_at")
    if not opened_at:
        return True
    try:
        parsed = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age = (now - parsed.astimezone(UTC)).total_seconds()
    return age > max_age_seconds


def masked_login(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]
