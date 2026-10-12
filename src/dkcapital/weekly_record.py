"""Weekly broker-confirmed performance reporting for the XAUUSD demo account."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dkcapital.mt5_account_reporting import summarize

LONDON = ZoneInfo("Europe/Isle_of_Man")
NEW_YORK = ZoneInfo("America/New_York")


def close_time(now: datetime) -> datetime:
    """Most recent Friday 17:00 New York time, accounting for US DST."""
    ny = now.astimezone(NEW_YORK)
    days_back = (ny.weekday() - 4) % 7
    friday = (ny - timedelta(days=days_back)).replace(
        hour=17, minute=0, second=0, microsecond=0)
    if friday > ny:
        friday -= timedelta(days=7)
    return friday.astimezone(UTC)


def record_due(now: datetime) -> bool:
    """Publish after Friday's forex-market close, with five minutes' grace."""
    closing = close_time(now)
    return closing <= now.astimezone(UTC) - timedelta(minutes=5) and (
        now.astimezone(UTC) - closing < timedelta(hours=48)
    )


def weekly_record(snapshot: dict, *, now: datetime, pip_size: float,
                  starting_balance: float | None) -> dict:
    closing = close_time(now)
    generated = datetime.fromisoformat(str(snapshot["generated_at"]).replace("Z", "+00:00"))
    if generated.tzinfo is None or not closing - timedelta(minutes=30) <= generated <= closing + timedelta(minutes=15):
        raise ValueError("Missing sufficiently close MT5 market-close snapshot")
    summary = summarize(snapshot, pip_size=pip_size, now=closing)
    local_close = closing.astimezone(LONDON)
    week_start = (local_close - timedelta(days=local_close.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    groups = {
        int(d["group"]) for d in snapshot["deals"]
        if d["entry_type"] == "IN" and
        week_start <= datetime.fromisoformat(d["time"].replace("Z", "+00:00")) <= closing
    }
    return {
        "week": local_close.strftime("%Y-%m-%d"),
        "week_start": week_start.isoformat(),
        "market_close": closing.isoformat(),
        "starting_balance": starting_balance,
        "ending_balance": summary["balance"],
        "realized_pnl": summary["week_realized_gbp"],
        "weekly_pips": summary["week_realized_pips"],
        "trades_taken": len(groups),
        "closed_signals": summary["week_closed_signals"],
        "close_count": summary["week_closed_legs"],
    }
