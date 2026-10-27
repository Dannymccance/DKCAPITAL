"""Read-only account 2/3 execution-quality checks.

No order submission or position modifications. Broker telemetry is authoritative
for fills. Incomplete history is reported as unknown, not as a failed trade.
"""
from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group

QUIET_SECONDS = {"gws": 45, "elite": 20, "money_house": 45}


def edit_ready(signal: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Provider-specific advisory readiness; manage open positions regardless."""
    now = now or datetime.now(UTC)
    try:
        stamp = datetime.fromisoformat(str(signal.get("last_update_at") or signal["opened_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone missing")
        provider = str(signal.get("source_style") or "").lower()
        quiet = QUIET_SECONDS.get(provider, 30)
        age = (now - stamp).total_seconds()
        return {"ready": age >= quiet, "quiet_seconds": quiet, "seconds_since_edit": round(age, 1)}
    except (ValueError, KeyError, TypeError, OverflowError):
        return {"ready": False, "reason": "invalid_edit_timestamp"}


def planned_rr(signal: dict[str, Any]) -> dict[str, Any]:
    """Equal nominal target weights; not a broker execution or profit guarantee."""
    try:
        direction = signal["direction"].upper()
        lo, hi = float(signal["entry_low"]), float(signal["entry_high"])
        stop = float(signal["current_sl"])
        targets = signal["tps"]
        if direction not in ("BUY", "SELL") or not isinstance(targets, dict):
            raise ValueError()
        indexes = sorted(int(k) for k in targets)
        if not indexes or indexes != list(range(1, len(indexes) + 1)):
            raise ValueError()
        values = [float(targets[str(i)]) for i in indexes]
        if not all(isfinite(x) and x > 0 for x in [lo, hi, stop, *values]):
            raise ValueError()
        entry = hi if direction == "BUY" else lo
        risk = (entry - stop) if direction == "BUY" else (stop - entry)
        if risk <= 0:
            raise ValueError()
        rewards = [(tp - entry) if direction == "BUY" else (entry - tp) for tp in values]
        if any(r <= 0 for r in rewards):
            raise ValueError()
        return {"rr": round(sum(rewards) / len(rewards) / risk, 4),
                "tp_count": len(values), "basis": "worst_zone_equal_weights_pre_costs",
                "single_tp_below_1r": len(values) == 1 and rewards[0] < risk}
    except (KeyError, TypeError, ValueError, OverflowError):
        return {"rr": None, "reason": "invalid_or_incomplete_plan"}


def execution_quality(signals: list[dict[str, Any]], snapshot: dict[str, Any],
                      *, now: datetime | None = None) -> dict[str, Any]:
    """Advisory diagnostics on authenticated, recent account telemetry only."""
    now = now or datetime.now(UTC)
    try:
        seen = datetime.fromisoformat(str(snapshot["received_at"]).replace("Z", "+00:00"))
        if (seen.tzinfo is None or not -15 <= (now - seen).total_seconds() <= 120
                or snapshot.get("version") != 2):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        return {"verified": False, "warnings": [], "signals": []}
    positions = snapshot.get("positions", [])
    if not isinstance(positions, list):
        return {"verified": False, "warnings": [], "signals": []}
    warnings: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []
    provider_directions: dict[str, set[str]] = {}
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("symbol") != "XAUUSD":
            continue
        sid = str(signal.get("signal_id") or "")
        if not sid:
            continue
        group = mt5_group(sid)
        held = [p for p in positions if isinstance(p, dict) and p.get("group") == group]
        rr = planned_rr(signal)
        readiness = edit_ready(signal, now)
        provider = str(signal.get("source_style") or "unknown")
        expected = rr.get("tp_count")
        actual = len(held)
        broker_entry_legs = {
            int(d.get("magic", 0)) % 10 for d in snapshot.get("deals", [])
            if isinstance(d, dict) and d.get("group") == group
            and d.get("entry_type") == "IN"
            and int(d.get("magic", 0)) % 10 in (1, 2, 3, 5, 6, 7)
        }
        # Compare historical fills where visible. Never classify missing
        # historical legs as definitely rejected: deal windows are bounded.
        observed_legs = broker_entry_legs | {
            int(p.get("leg", 0)) for p in held if int(p.get("leg", 0)) in (1, 2, 3, 5, 6, 7)
        }
        entry_prices = [float(p["entry"]) for p in held if p.get("entry") is not None]
        if held:
            provider_directions.setdefault(provider, set()).add(str(signal.get("direction")))
            if expected is not None and len(observed_legs) < expected:
                warnings.append({"code": "BROKER_LEG_HISTORY_INCOMPLETE", "signal_id": sid,
                                 "expected_current_targets": expected,
                                 "observed_entry_legs": len(observed_legs),
                                 "note": "History window or Telegram edits can explain mismatch; not proof of a failed fill"})
            for p in held:
                if float(p.get("sl") or 0) <= 0:
                    warnings.append({"code": "MISSING_BROKER_STOP", "signal_id": sid,
                                     "ticket": p.get("ticket")})
        if not readiness.get("ready") and held:
            warnings.append({"code": "OPEN_POSITION_MANAGEMENT_MUST_CONTINUE", "signal_id": sid})
        theoretical = None
        if signal.get("entry_low") is not None and signal.get("entry_high") is not None:
            lo, hi = float(signal["entry_low"]), float(signal["entry_high"])
            theoretical = hi if signal.get("direction") == "SELL" else lo
        details.append({"signal_id": sid, "provider": provider, "readiness": readiness,
                        "planned": rr, "open_legs": actual, "observed_entry_legs": len(observed_legs),
                        "expected_targets": expected,
                        "actual_average_entry": round(sum(entry_prices)/len(entry_prices), 5)
                            if entry_prices else None,
                        "hypothetical_best_zone_entry": theoretical,
                        "entry_comparison_is_shadow_only": True})
    open_sides = set().union(*provider_directions.values()) if provider_directions else set()
    if "BUY" in open_sides and "SELL" in open_sides:
        warnings.append({"code": "CONFLICTING_OPEN_DIRECTIONS",
                         "note": "Advisory only; no automatic closing or entry blocking"})
    return {"verified": True, "warnings": warnings, "signals": details,
            "breakeven_net_profit_floor_account_currency": 0.30,
            "breakeven_floor_enforced": False,
            "note": "A 0.30 net BE stop needs broker cost/contract valuation and execution-side support; telemetry cannot move SL."}
