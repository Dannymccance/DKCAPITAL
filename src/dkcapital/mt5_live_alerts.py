"""Read-only broker-telemetry alert rules for independently monitored REAL MT5 accounts.

This module neither submits orders nor restarts trading terminals.
It consumes authenticated gateway snapshots, never MT5 credentials.
"""
from __future__ import annotations

from datetime import UTC, datetime
from math import isfinite
from typing import Any


def trading_permission_issues(snapshot: dict[str, Any], prefix: str) -> set[str]:
    """Report independent permission gates; missing readings are UNKNOWN.

    Older EA versions cannot prove permission is enabled. The reporting layer
    intentionally does not infer permissions from entry_enabled or past fills.
    """
    issues: set[str] = set()
    for field, suffix in (
        ("terminal_trade_allowed", "TERMINAL_ALGO_TRADING_DISABLED"),
        ("mql_trade_allowed", "EA_ALGO_TRADING_DISABLED"),
    ):
        value = snapshot.get(field)
        if value is False:
            issues.add(f"{prefix}_{suffix}")
        elif value is not True:
            issues.add(f"{prefix}_{suffix.replace('_DISABLED', '_UNVERIFIED')}")
    return issues


STALE_SECONDS = 150
HOST_STALE_SECONDS = 180
RECENT_RESTART_SECONDS = 300


def _age(value: Any, now: datetime) -> float:
    if not isinstance(value, str):
        return float("inf")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return float("inf")
        return max(0.0, (now - stamp.astimezone(UTC)).total_seconds())
    except (ValueError, OverflowError):
        return float("inf")


def live_account_issues(
    snapshot: dict[str, Any], now: datetime, *,
    account_id: int, login: int, mode: str, currency: str, server: str,
    expect_entries: bool | None = None,
    host: dict[str, Any] | None = None,
) -> set[str]:
    """Detect exactly the same failures for Account 2 or 3, with isolated codes."""
    if account_id not in (2, 3):
        raise ValueError("Live account alerts require Account 2 or 3")
    PREFIX = f"MT5_ACCOUNT{account_id}"
    if login <= 0 or not server or mode not in {"DEMO", "REAL"} or currency not in {"GBP", "USD"}:
        return {f"{PREFIX}_ALERT_CONFIGURATION_INVALID"}
    problems: set[str] = set()
    if _age(snapshot.get("received_at"), now) > STALE_SECONDS:
        problems.add(f"{PREFIX}_TELEMETRY_STALE")
    else:
        if snapshot.get("login") != login:
            problems.add(f"{PREFIX}_WRONG_LOGIN")
        if snapshot.get("mode") != mode or snapshot.get("currency") != currency:
            problems.add(f"{PREFIX}_WRONG_ACCOUNT_TYPE")
        if snapshot.get("server") != server:
            problems.add(f"{PREFIX}_WRONG_BROKER_SERVER")
        problems.update(trading_permission_issues(snapshot, PREFIX))
        if snapshot.get("feed_http") != 200:
            problems.add(f"{PREFIX}_INSTRUCTION_FEED_UNAVAILABLE")
        if expect_entries is not None and snapshot.get("entry_enabled") is not expect_entries:
            problems.add(
                f"{PREFIX}_UNEXPECTEDLY_DISARMED" if expect_entries
                else f"{PREFIX}_UNEXPECTEDLY_ARMED"
            )
        for k in ("broker_positions_total", "broker_orders_total"):
            if type(snapshot.get(k)) is not int or snapshot[k] < 0:
                problems.add(f"{PREFIX}_EXPOSURE_UNVERIFIED")
                break
        if snapshot.get("broker_positions_protected") is not True:
            problems.add(f"{PREFIX}_BROKER_STOPS_UNVERIFIED")
        cap, balance = snapshot.get("signal_risk_cap_account_currency"), snapshot.get("balance")
        pct = snapshot.get("signal_risk_cap_pct")
        if (type(cap) in (int, float) and type(balance) in (int, float)
                and type(pct) in (int, float)
                and all(isfinite(float(v)) for v in (cap, balance, pct))
                and balance > 0 and pct > 0):
            if cap > balance * pct / 100 + 0.02:
                problems.add(f"{PREFIX}_RISK_CAP_EXCEEDS_CASH_BALANCE")
    if host is not None and host and _age(host.get("checked_at"), now) <= HOST_STALE_SECONDS:
        info = (host.get("accounts") or {}).get(str(account_id))
        if isinstance(info, dict):
            if info.get("active") is not True:
                problems.add(f"{PREFIX}_TERMINAL_DOWN")
            if _age(info.get("last_restart_at"), now) < RECENT_RESTART_SECONDS:
                problems.add(f"{PREFIX}_TERMINAL_RECENTLY_RESTARTED")
    return problems

def account3_issues(
    snapshot: dict[str, Any], now: datetime, *,
    login: int, mode: str, currency: str, server: str,
    expect_entries: bool | None = None,
    host: dict[str, Any] | None = None,
) -> set[str]:
    """Backward-compatible Dan Account 3 alert contract."""
    return live_account_issues(
        snapshot, now, account_id=3, login=login, mode=mode,
        currency=currency, server=server,
        expect_entries=expect_entries, host=host,
    )


def account2_issues(
    snapshot: dict[str, Any], now: datetime, *,
    login: int, mode: str, currency: str, server: str,
    expect_entries: bool | None = None,
    host: dict[str, Any] | None = None,
) -> set[str]:
    """Kyle Account 2 uses the identical broker checks as Dan."""
    return live_account_issues(
        snapshot, now, account_id=2, login=login, mode=mode,
        currency=currency, server=server,
        expect_entries=expect_entries, host=host,
    )
