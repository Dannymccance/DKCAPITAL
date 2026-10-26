from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging
from dkcapital.mt5_account_reporting import summarize
from dkcapital.mt5_pip_history import record_weekly_average
from dkcapital.mt5_signal_cards import broker_signal_state, mt5_group, mt5_signal_embed
from dkcapital.mt5_trade_results import completed_results, result_embed, verified_snapshot
from dkcapital.mt5_broker_events import discover_events, notification_embed
from dkcapital.telegram_discord_mirror import chats_channel_id, sync_mirror
from dkcapital.weekly_record import close_time, record_due, weekly_record

logger = logging.getLogger("dkcapital.discord")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _new_signal_card_eligible(signal: dict[str, Any], cutover: str) -> bool:
    """Use first successful parse time, not original Telegram post time.

    Telegram's original date remains unchanged for broker entry-age checks.
    Historical posts first parsed before Discord's cutover stay suppressed.
    """
    candidate = _local_datetime(
        signal.get("publish_ready_at") or signal.get("opened_at"), "UTC"
    )
    boundary = _local_datetime(cutover, "UTC")
    return candidate is not None and boundary is not None and candidate > boundary


def _ordered_signals_for_discord(
    signals: list[dict[str, Any]], messages: dict[str, Any]
) -> list[dict[str, Any]]:
    """Send missing cards before edits when the per-cycle edit budget is tight."""
    return sorted(signals, key=lambda signal: (
        str(signal.get("signal_id") or "") in messages,
        str(signal.get("publish_ready_at") or signal.get("opened_at") or ""),
        str(signal.get("signal_id") or ""),
    ))


def _paper_market_quote(state: dict[str, Any]) -> dict[str, Any] | None:
    simulation = state.get("simulation") if isinstance(state.get("simulation"), dict) else {}
    if simulation.get("enabled") and not simulation.get("last_market_processed_at"):
        return None
    price = state.get("last_mark_price")
    if price is None:
        return None
    bid = state.get("last_bid")
    ask = state.get("last_ask")
    return {
        "symbol": "XAUUSD",
        "price": float(price),
        "bid": float(bid) if bid is not None else float(price),
        "ask": float(ask) if ask is not None else float(price),
        "computed_at": state.get("last_mark_at"),
        "is_stale": False,
        "source": "standard-bullion:XAUUSD",
        "source_url": "https://standardbullion.com",
        "attribution": "Data by Standard Bullion",
        "delay_seconds": int(
            ((state.get("simulation") or {}).get("delay_seconds") or 0)
        ),
        "simulation_time": (state.get("simulation") or {}).get("simulation_time"),
    }


def _price(value: Any) -> str:
    if value is None:
        return "Not set"
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _local_datetime(value: Any, timezone_name: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(ZoneInfo(timezone_name))
    except (TypeError, ValueError, KeyError):
        return None


def _local_time(value: Any, timezone_name: str) -> str:
    parsed = _local_datetime(value, timezone_name)
    if parsed is None:
        return "Unknown time"
    return parsed.strftime("%H:%M %Z")


def _local_timestamp(value: Any, timezone_name: str) -> str:
    parsed = _local_datetime(value, timezone_name)
    if parsed is None:
        return str(value or "")
    return parsed.strftime("%d %b %Y %H:%M:%S %Z")


def _entry_anchor(signal: dict[str, Any]) -> float:
    low = float(signal.get("entry_low") or 0.0)
    high = float(signal.get("entry_high") or low)
    return (low + high) / 2.0


def _move_points(signal: dict[str, Any], price: float) -> float:
    entry = _entry_anchor(signal)
    if str(signal.get("direction") or "").upper() == "SELL":
        return entry - price
    return price - entry


def _risk_points(signal: dict[str, Any]) -> float | None:
    original_sl = signal.get("original_sl")
    if original_sl is None:
        return None
    risk = abs(_entry_anchor(signal) - float(original_sl))
    return risk if risk > 0 else None


def _signed(value: float, suffix: str = "") -> str:
    return f"{value:+.2f}{suffix}"


def _history_summary(
    signal: dict[str, Any],
    timezone_name: str,
) -> tuple[list[str], float, float | None, bool]:
    history = list(signal.get("history") or [])
    if not history:
        return [], 0.0, None, False

    risk = _risk_points(signal)
    remaining = 1.0
    realised_weighted_points = 0.0
    realised_r = 0.0 if risk is not None else None
    missing_realised_price = False
    grouped: list[dict[str, Any]] = []
    index: dict[tuple[str, Any], dict[str, Any]] = {}

    for item in history:
        timestamp = str(item.get("timestamp") or "")
        message_id = item.get("message_id")
        key = (timestamp, message_id)
        group = index.get(key)
        if group is None:
            group = {
                "timestamp": timestamp,
                "message_id": message_id,
                "parts": [],
                "market_price": None,
                "realised_r": 0.0,
                "has_realised": False,
            }
            index[key] = group
            grouped.append(group)

        market = item.get("market") if isinstance(item.get("market"), dict) else None
        market_price = None
        if market and market.get("price") is not None:
            market_price = float(market["price"])
            group["market_price"] = market_price

        kind = str(item.get("kind") or "")
        if kind == "opened":
            sl = item.get("sl")
            text = f"Opened {signal.get('direction')} @ {_price(signal.get('entry_low'))}"
            if sl is not None:
                text += f" | SL {_price(sl)}"
            group["parts"].append(text)
            continue

        if kind == "partial_close":
            partial_percent = float(item.get("partial_percent") or 0.0)
            close_fraction = remaining * max(0.0, min(1.0, partial_percent / 100.0))
            if market_price is not None and close_fraction > 0:
                weighted_points = _move_points(signal, market_price) * close_fraction
                realised_weighted_points += weighted_points
                if risk is not None:
                    event_r = weighted_points / risk
                    realised_r = float(realised_r or 0.0) + event_r
                    group["realised_r"] += event_r
                    group["has_realised"] = True
            elif close_fraction > 0:
                missing_realised_price = True

            remaining *= 1.0 - max(0.0, min(1.0, partial_percent / 100.0))
            group["parts"].append(
                f"Closed {partial_percent:g}% of remainder | Remaining {remaining * 100:.2f}%"
            )
            continue

        if kind == "trade_update":
            if item.get("move_sl_to_be"):
                group["parts"].append("SL -> BE")
            elif item.get("sl_after") is not None:
                before = item.get("sl_before")
                after = item.get("sl_after")
                if before is None or float(before) != float(after):
                    group["parts"].append(f"SL -> {_price(after)}")
            if item.get("tp_hits"):
                hits = ", ".join(f"TP{int(v)}" for v in item["tp_hits"])
                group["parts"].append(f"Hit {hits}")
            continue

        if kind in {"close", "stop_loss", "setup_failed", "breakeven_close", "cancel"}:
            if kind != "cancel" and remaining > 0:
                if market_price is not None:
                    weighted_points = _move_points(signal, market_price) * remaining
                    realised_weighted_points += weighted_points
                    if risk is not None:
                        event_r = weighted_points / risk
                        realised_r = float(realised_r or 0.0) + event_r
                        group["realised_r"] += event_r
                        group["has_realised"] = True
                else:
                    missing_realised_price = True
            remaining = 0.0
            label = {
                "close": "Closed",
                "stop_loss": "Stopped",
                "setup_failed": "Setup failed",
                "breakeven_close": "Closed at BE",
                "cancel": "Cancelled",
            }[kind]
            group["parts"].append(label)
            continue

        if kind == "add_layer":
            group["parts"].append("Layer added")
        elif kind == "reenter":
            group["parts"].append("Re-entry")

    lines: list[str] = []
    for group in grouped:
        parts = list(group["parts"])
        if not parts:
            continue
        prefix = _local_time(group["timestamp"], timezone_name)
        price_text = (
            f" @ {_price(group['market_price'])}"
            if group.get("market_price") is not None
            and not any(part.startswith("Opened ") for part in parts)
            else ""
        )
        realised_text = (
            f" | Realised {_signed(float(group['realised_r']), 'R')}"
            if group.get("has_realised")
            else ""
        )
        lines.append(
            f"**{prefix}** | " + " | ".join(parts) + price_text + realised_text
        )

    return lines[-8:], realised_weighted_points, realised_r, missing_realised_price


def _signal_pnl_text(
    signal: dict[str, Any],
    spot_quote: dict[str, Any] | None,
    timezone_name: str,
) -> tuple[str, str, str]:
    _, _, realised_r, missing_realised = _history_summary(signal, timezone_name)
    risk = _risk_points(signal)

    if realised_r is None:
        realised_text = "Not available - original risk is not set"
    else:
        realised_text = _signed(realised_r, "R")
        if missing_realised:
            realised_text += " + unpriced legacy exits"

    if (
        spot_quote is None
        or spot_quote.get("price") is None
        or str(signal.get("status") or "") != "ACTIVE"
        or risk is None
    ):
        floating_text = "Not available"
        total_text = realised_text
    else:
        spot = float(spot_quote["price"])
        remaining = float(signal.get("remaining_fraction", 1.0) or 0.0)
        floating_r = (_move_points(signal, spot) * remaining) / risk
        floating_text = _signed(floating_r, "R")
        if realised_r is None:
            total_text = floating_text
        else:
            total_text = _signed(realised_r + floating_r, "R")
            if missing_realised:
                total_text += " + unpriced legacy exits"

    return realised_text, floating_text, total_text


def _paper_signed_usd(value: float) -> str:
    return f"${value:+,.2f}"


def _paper_pips(
    direction: str,
    entry_price: float,
    mark_price: float,
    pip_size: float,
) -> float:
    if pip_size <= 0:
        return 0.0
    move = (
        mark_price - entry_price
        if direction.upper() == "BUY"
        else entry_price - mark_price
    )
    return move / pip_size


def _paper_realised_pips(position: dict[str, Any], pip_size: float) -> float:
    if pip_size <= 0:
        return 0.0
    entry = float(position.get("entry_price") or 0.0)
    initial_qty = float(position.get("initial_quantity_oz") or 0.0)
    direction = str(position.get("direction") or "").upper()
    if entry <= 0 or initial_qty <= 0:
        return 0.0

    weighted = 0.0
    for item in position.get("history") or []:
        if str(item.get("kind") or "") != "close":
            continue
        price = item.get("price")
        qty = item.get("quantity_oz")
        if price is None or qty is None:
            continue
        signed_pips = _paper_pips(
            direction,
            entry,
            float(price),
            pip_size,
        )
        weighted += signed_pips * float(qty)
    return weighted / initial_qty


def _paper_position_for_signal(
    paper_state: dict[str, Any],
    signal_id: str,
) -> dict[str, Any] | None:
    matches = [
        position
        for position in paper_state.get("positions") or []
        if str(position.get("signal_id") or "") == signal_id
    ]
    if not matches:
        return None

    open_matches = [
        position
        for position in matches
        if str(position.get("status") or "") == "OPEN"
    ]
    if open_matches:
        return open_matches[-1]
    return matches[-1]


def _paper_position_metrics(
    position: dict[str, Any],
    pip_size: float,
) -> dict[str, float]:
    entry = float(position.get("entry_price") or 0.0)
    initial_qty = float(position.get("initial_quantity_oz") or 0.0)
    remaining_qty = float(position.get("remaining_quantity_oz") or 0.0)
    mark = position.get("last_mark_price")
    direction = str(position.get("direction") or "").upper()

    realised_usd = float(position.get("realized_pnl_usd") or 0.0)
    open_usd = float(position.get("unrealized_pnl_usd") or 0.0)
    total_usd = realised_usd + open_usd

    realised_pips = _paper_realised_pips(position, pip_size)
    live_pips = (
        _paper_pips(direction, entry, float(mark), pip_size)
        if mark is not None and entry > 0
        else 0.0
    )
    remaining_fraction = (
        remaining_qty / initial_qty if initial_qty > 0 else 0.0
    )
    open_weighted_pips = live_pips * remaining_fraction
    total_weighted_pips = realised_pips + open_weighted_pips

    initial_risk = float(position.get("initial_risk_usd") or 0.0)
    realised_r = realised_usd / initial_risk if initial_risk > 0 else 0.0
    open_r = open_usd / initial_risk if initial_risk > 0 else 0.0
    total_r = total_usd / initial_risk if initial_risk > 0 else 0.0

    return {
        "realised_usd": realised_usd,
        "open_usd": open_usd,
        "total_usd": total_usd,
        "realised_pips": realised_pips,
        "live_pips": live_pips,
        "open_weighted_pips": open_weighted_pips,
        "total_weighted_pips": total_weighted_pips,
        "realised_r": realised_r,
        "open_r": open_r,
        "total_r": total_r,
        "remaining_fraction": remaining_fraction,
    }


def _paper_trade_stats(state: dict[str, Any], pip_size: float) -> dict[str, Any]:
    positions = list(state.get("positions") or [])
    closed = [p for p in positions if str(p.get("status") or "") == "CLOSED"]
    wins = sum(1 for p in closed if float(p.get("realized_pnl_usd") or 0.0) > 1e-9)
    losses = sum(1 for p in closed if float(p.get("realized_pnl_usd") or 0.0) < -1e-9)
    breakeven = max(0, len(closed) - wins - losses)
    win_rate = (wins / len(closed) * 100.0) if closed else 0.0
    realised_pips = sum(_paper_realised_pips(p, pip_size) for p in closed)
    return {
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "realised_pips": realised_pips,
    }


def _paper_dashboard_embed(
    state: dict[str, Any],
    *,
    timezone_name: str = "Europe/Isle_of_Man",
    pip_size: float = 0.01,
    risk_pct: float = 0.005,
    direction_risk_cap_pct: float = 0.008,
    daily_loss_pct: float = 0.02,
) -> discord.Embed:
    starting = float(state.get("starting_balance_usd", 100000.0) or 0.0)
    balance = float(state.get("balance_usd", starting) or 0.0)
    equity = float(state.get("equity_usd", balance) or 0.0)
    realized = float(state.get("realized_pnl_usd", 0.0) or 0.0)
    unrealized = float(state.get("unrealized_pnl_usd", 0.0) or 0.0)
    total_pnl = equity - starting
    total_return = (total_pnl / starting * 100.0) if starting > 0 else 0.0

    peak_equity = float(state.get("peak_equity_usd", max(starting, equity)) or 0.0)
    current_dd = float(state.get("current_drawdown_usd", 0.0) or 0.0)
    current_dd_pct = float(state.get("current_drawdown_pct", 0.0) or 0.0)
    max_dd = float(state.get("max_drawdown_usd", 0.0) or 0.0)
    max_dd_pct = float(state.get("max_drawdown_pct", 0.0) or 0.0)

    positions = list(state.get("positions") or [])
    open_positions = [
        p for p in positions if str(p.get("status") or "") == "OPEN"
    ]
    candidates = list(state.get("candidates") or [])
    queued = sum(
        1
        for candidate in candidates
        if str(candidate.get("execution_status") or "")
        in {"PENDING_STRATEGY", "PENDING_SL", "PENDING_INVALID_SL"}
    )
    stats = _paper_trade_stats(state, pip_size)

    mode = str(state.get("strategy_mode") or "observe_only")
    entry_policy = str(state.get("entry_policy") or "all_parsed")
    simulation = state.get("simulation") if isinstance(state.get("simulation"), dict) else {}
    delay_seconds = int(simulation.get("delay_seconds") or 0)
    delay_minutes = delay_seconds / 60.0
    simulation_time = simulation.get("simulation_time")
    warming_up = bool(simulation.get("warming_up", False))
    mark = state.get("last_mark_price")
    directional_risk = state.get("directional_risk_usd") or {}
    buy_risk = float(directional_risk.get("BUY", 0.0) or 0.0)
    sell_risk = float(directional_risk.get("SELL", 0.0) or 0.0)
    day_start = float(state.get("day_start_balance_usd", balance) or balance)
    daily_floor = float(state.get("daily_equity_floor_usd", day_start) or day_start)
    daily_stop = bool(state.get("daily_stop_triggered", False))
    floor_buffer = equity - daily_floor

    embed = discord.Embed(
        title="DK Capital | XAUUSD Paper Trading",
        description=(
            f"**$100k Paper Account** | Entry policy: **{entry_policy.upper()}**\n"
            f"Strategy mode: **{mode}**"
            + (
                f"\nSimulation: **{delay_minutes:g} min delayed**"
                if delay_seconds > 0
                else ""
            )
        ),
    )

    embed.add_field(
        name="Account",
        value=(
            f"Starting: **${starting:,.2f}**\n"
            f"Balance: **${balance:,.2f}**\n"
            f"Live equity: **${equity:,.2f}**\n"
            f"Total PnL: **{_paper_signed_usd(total_pnl)}** ({total_return:+.2f}%)"
        ),
        inline=True,
    )
    embed.add_field(
        name="PnL",
        value=(
            f"Realised: **{_paper_signed_usd(realized)}**\n"
            f"Open: **{_paper_signed_usd(unrealized)}**\n"
            f"Realised pips: **{stats['realised_pips']:+,.1f}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Drawdown",
        value=(
            f"Peak equity: **${peak_equity:,.2f}**\n"
            f"Current: **${current_dd:,.2f}** ({current_dd_pct:.2f}%)\n"
            f"Maximum: **${max_dd:,.2f}** ({max_dd_pct:.2f}%)"
        ),
        inline=True,
    )

    simulation_text = "Not configured"
    if delay_seconds > 0 and simulation_time:
        simulation_text = (
            f"**{_local_timestamp(simulation_time, timezone_name)}**\n"
            f"Delay: **{delay_minutes:g} minutes**\n"
            f"Status: **{'BUFFERING' if warming_up else 'ACTIVE'}**"
        )
    elif simulation_time:
        simulation_text = f"**{_local_timestamp(simulation_time, timezone_name)}**"
    embed.add_field(
        name="Simulation Clock",
        value=simulation_text,
        inline=True,
    )

    mark_text = "Buffering XAU/USD tape..." if warming_up else "Unavailable"
    if mark is not None and (not simulation.get("enabled") or simulation.get("last_market_processed_at")):
        mark_text = f"**${float(mark):,.2f}**"
        marked_at = state.get("last_mark_at")
        if marked_at:
            mark_text += f"\n{_local_timestamp(marked_at, timezone_name)}"
        mark_text += "\n[Data by Standard Bullion](https://standardbullion.com)"
    embed.add_field(
        name=("XAU/USD Delayed Mark" if delay_seconds > 0 else "XAU/USD Live Mark"),
        value=mark_text,
        inline=True,
    )

    embed.add_field(
        name="Risk Controls",
        value=(
            f"Per trade: **{risk_pct * 100:.2f}%**\n"
            "Per direction: **DISABLED**\n"
            f"Daily stop: **{daily_loss_pct * 100:.2f}%**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Daily Risk",
        value=(
            f"Day start: **${day_start:,.2f}**\n"
            f"Equity floor: **${daily_floor:,.2f}**\n"
            f"Buffer: **{_paper_signed_usd(floor_buffer)}** | "
            f"{'STOPPED' if daily_stop else 'ACTIVE'}"
        ),
        inline=True,
    )
    embed.add_field(
        name="Directional Risk",
        value=(
            f"BUY book: **${buy_risk:,.2f}**\n"
            f"SELL book: **${sell_risk:,.2f}**\n"
            "Aggregate cap: **DISABLED**"
        ),
        inline=True,
    )

    embed.add_field(
        name="Trade Stats",
        value=(
            f"Open: **{len(open_positions)}** | Closed: **{stats['closed']}**\n"
            f"W/L/BE: **{stats['wins']} / {stats['losses']} / {stats['breakeven']}**\n"
            f"Win rate: **{stats['win_rate']:.1f}%**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Signal Intake",
        value=(
            f"Parsed XAUUSD: **{len(candidates)}**\n"
            f"Queued / waiting: **{queued}**\n"
            f"Pip size: **{pip_size:g}** ($1.00 = {1.0 / pip_size:,.0f} pips)"
        ),
        inline=True,
    )

    decision_candidates = [
        candidate
        for candidate in candidates
        if str(candidate.get("execution_status") or "")
        not in {"OPEN", "CLOSED", "SKIPPED_PRE_STRATEGY"}
    ][-5:]
    if decision_candidates:
        decision_lines: list[str] = []
        for candidate in reversed(decision_candidates):
            status = str(candidate.get("execution_status") or "UNKNOWN")
            message_id = candidate.get("root_message_id")
            note = str(candidate.get("execution_note") or "").strip()
            prefix = f"TG {message_id} | " if message_id is not None else ""
            line = (
                f"**{prefix}{candidate.get('direction')} XAUUSD** - "
                f"`{status}`"
            )
            if note:
                line += f"\n{note}"
            decision_lines.append(line)
        decisions = "\n\n".join(decision_lines)
        if len(decisions) > 1024:
            decisions = decisions[:1018] + "\n..."
        embed.add_field(
            name="Recent Signal Decisions",
            value=decisions,
            inline=False,
        )

    if open_positions:
        lines: list[str] = []
        for position in open_positions[-10:]:
            direction = str(position.get("direction") or "?").upper()
            entry = float(position.get("entry_price") or 0.0)
            live_mark = position.get("last_mark_price")
            if live_mark is None:
                live_mark = mark
            current_pips = (
                _paper_pips(direction, entry, float(live_mark), pip_size)
                if live_mark is not None and entry > 0
                else 0.0
            )
            open_pnl = float(position.get("unrealized_pnl_usd") or 0.0)
            realised_trade = float(position.get("realized_pnl_usd") or 0.0)
            realised_trade_pips = _paper_realised_pips(position, pip_size)
            initial_qty = float(position.get("initial_quantity_oz") or 0.0)
            remaining_qty = float(position.get("remaining_quantity_oz") or 0.0)
            remaining_pct = (
                remaining_qty / initial_qty * 100.0 if initial_qty > 0 else 0.0
            )
            stop = position.get("stop_loss")
            stop_text = "None" if stop is None else _price(stop)
            if bool(position.get("temporary_stop_active")) and stop is not None:
                stop_text = f"TEMP {stop_text}"
            elif str(position.get("stop_source") or "") == "BREAKEVEN":
                stop_text = f"BE {stop_text}"
            lots = float(position.get("lot_size") or 0.0)
            initial_risk = float(position.get("initial_risk_usd") or 0.0)
            if stop is None:
                current_risk = 0.0
            elif direction == "BUY":
                current_risk = max(0.0, entry - float(stop)) * remaining_qty
            else:
                current_risk = max(0.0, float(stop) - entry) * remaining_qty
            signal_id = str(position.get("signal_id") or "")
            candidate = next(
                (
                    c
                    for c in candidates
                    if str(c.get("signal_id") or "") == signal_id
                ),
                None,
            )
            provider = _provider_name(candidate or {})
            lines.append(
                f"**{direction} XAUUSD** | {provider}\n"
                f"Entry {_price(entry)} -> Mark {_price(live_mark)} | SL {stop_text}\n"
                f"Open **{_paper_signed_usd(open_pnl)} / {current_pips:+,.1f} pips** | "
                f"Realised **{_paper_signed_usd(realised_trade)} / "
                f"{realised_trade_pips:+,.1f} pips**\n"
                f"Size **{lots:.2f} lots** | {remaining_qty:,.2f}/{initial_qty:,.2f} oz "
                f"({remaining_pct:.1f}% remaining)\n"
                f"Initial risk **${initial_risk:,.2f}** | Current risk **${current_risk:,.2f}**"
            )

        if len(open_positions) > 10:
            lines.append(f"...and {len(open_positions) - 10} more open trade(s).")
        current_text = "\n\n".join(lines)
        if len(current_text) > 1024:
            current_text = current_text[:1018] + "\n..."
        embed.add_field(name="Current Trades", value=current_text, inline=False)
    else:
        embed.add_field(
            name="Current Trades",
            value=(
                "No open paper trades. Every new valid parsed XAUUSD signal is "
                "eligible for immediate strategy execution."
            ),
            inline=False,
        )

    closed_positions = [
        p for p in positions if str(p.get("status") or "") == "CLOSED"
    ]
    if closed_positions:
        recent_lines: list[str] = []
        for position in closed_positions[-5:][::-1]:
            pnl = float(position.get("realized_pnl_usd") or 0.0)
            pips = _paper_realised_pips(position, pip_size)
            recent_lines.append(
                f"{position.get('direction')} XAUUSD @ {_price(position.get('entry_price'))} "
                f"| **{_paper_signed_usd(pnl)} / {pips:+,.1f} pips**"
            )
        embed.add_field(
            name="Recent Closed Trades",
            value="\n".join(recent_lines),
            inline=False,
        )

    updated = state.get("updated_at")
    footer = "PAPER TRADING ONLY"
    if updated:
        footer += f" | Updated {_local_timestamp(updated, timezone_name)}"
    embed.set_footer(text=footer)
    return embed


def _paper_status_embed(
    state: dict[str, Any],
    *,
    timezone_name: str = "Europe/Isle_of_Man",
    pip_size: float = 0.01,
    risk_pct: float = 0.005,
    direction_risk_cap_pct: float = 0.008,
    daily_loss_pct: float = 0.02,
) -> discord.Embed:
    return _paper_dashboard_embed(
        state,
        timezone_name=timezone_name,
        pip_size=pip_size,
        risk_pct=risk_pct,
        direction_risk_cap_pct=direction_risk_cap_pct,
        daily_loss_pct=daily_loss_pct,
    )

def _dashboard_embed(state: dict[str, Any]) -> discord.Embed:
    signals = list(state.get("signals") or [])
    active = [signal for signal in signals if signal.get("status") == "ACTIVE"]
    recent = [signal for signal in signals if signal.get("status") != "ACTIVE"][-5:]

    embed = discord.Embed(
        title="DK Capital | Live Trade Dashboard",
        description="Automatically maintained from the DK Capital source feed.",
    )

    if not active:
        embed.add_field(
            name="Active trades",
            value="No active tracked trades.",
            inline=False,
        )
    else:
        for signal in active[-12:]:
            low = _price(signal.get("entry_low"))
            high = _price(signal.get("entry_high"))
            entry = low if low == high else f"{low} - {high}"
            direction = str(signal.get("direction") or "?")
            symbol = str(signal.get("symbol") or "?")

            tps = signal.get("tps") or {}
            hits = {int(value) for value in signal.get("tp_hits") or []}
            tp_lines: list[str] = []
            for raw_index, target in sorted(tps.items(), key=lambda item: int(item[0])):
                index = int(raw_index)
                marker = "✅" if index in hits else "⬜"
                tp_lines.append(f"{marker} TP{index}: {_price(target)}")

            if signal.get("sl_mode") == "BREAKEVEN":
                sl_text = "BE"
            elif signal.get("sl_mode") == "UNSET":
                sl_text = "Not set"
            else:
                sl_text = _price(signal.get("current_sl"))

            details = tp_lines or ["Targets not supplied"]
            details.append(f"SL: **{sl_text}**")
            details.append(
                f"Layers: {signal.get('layers', 1)} | Re-entries: {signal.get('reentries', 0)}"
            )
            partials = int(signal.get("partial_close_count", 0) or 0)
            if partials:
                remaining = float(signal.get("remaining_fraction", 1.0) or 0.0) * 100
                details.append(
                    f"Partials: {partials} | Remaining: {remaining:.2f}%"
                )
            if signal.get("layer_max") is not None:
                details.append(f"Layer max: {_price(signal.get('layer_max'))}")

            embed.add_field(
                name=f"{direction} {symbol} | {entry}",
                value="\n".join(details),
                inline=False,
            )

    if recent:
        lines = []
        for signal in reversed(recent):
            lines.append(
                f"`{signal.get('status')}` {signal.get('direction')} {signal.get('symbol')} "
                f"{_price(signal.get('entry_low'))}-{_price(signal.get('entry_high'))}"
            )
        embed.add_field(name="Recent completed/closed", value="\n".join(lines), inline=False)

    unresolved = len(state.get("unresolved_actions") or [])
    updated = state.get("updated_at")
    footer = f"Parser exceptions awaiting review: {unresolved}"
    if updated:
        footer += f" | State updated {updated}"
    embed.set_footer(text=footer)
    return embed


def _provider_name(signal: dict[str, Any]) -> str:
    style = str(signal.get("source_style") or "").lower()
    if style == "elite":
        return "Elite Portfolios"
    if style == "gws":
        return "GWS"
    return "Telegram Provider"


def _signal_fingerprint(
    signal: dict[str, Any],
    spot_quote: dict[str, Any] | None = None,
    paper_position: dict[str, Any] | None = None,
) -> str:
    relevant = {
        "direction": signal.get("direction"),
        "symbol": signal.get("symbol"),
        "entry_low": signal.get("entry_low"),
        "entry_high": signal.get("entry_high"),
        "tps": signal.get("tps"),
        "current_sl": signal.get("current_sl"),
        "sl_mode": signal.get("sl_mode"),
        "tp_hits": signal.get("tp_hits"),
        "layers": signal.get("layers"),
        "reentries": signal.get("reentries"),
        "layer_max": signal.get("layer_max"),
        "remaining_fraction": signal.get("remaining_fraction"),
        "partial_close_count": signal.get("partial_close_count"),
        "status": signal.get("status"),
        "last_update_at": signal.get("last_update_at"),
        "history": signal.get("history"),
        "spot_price": None if spot_quote is None else spot_quote.get("price"),
        "spot_computed_at": None if spot_quote is None else spot_quote.get("computed_at"),
        "paper_position": (
            None
            if paper_position is None
            else {
                "status": paper_position.get("status"),
                "entry_price": paper_position.get("entry_price"),
                "lot_size": paper_position.get("lot_size"),
                "initial_quantity_oz": paper_position.get("initial_quantity_oz"),
                "remaining_quantity_oz": paper_position.get("remaining_quantity_oz"),
                "stop_loss": paper_position.get("stop_loss"),
                "stop_source": paper_position.get("stop_source"),
                "temporary_stop_active": paper_position.get("temporary_stop_active"),
                "last_mark_price": paper_position.get("last_mark_price"),
                "unrealized_pnl_usd": paper_position.get("unrealized_pnl_usd"),
                "realized_pnl_usd": paper_position.get("realized_pnl_usd"),
                "initial_risk_usd": paper_position.get("initial_risk_usd"),
                "history": paper_position.get("history"),
            }
        ),
    }
    return json.dumps(relevant, sort_keys=True, separators=(",", ":"))


def _signal_embed(
    signal: dict[str, Any],
    *,
    spot_quote: dict[str, Any] | None = None,
    paper_position: dict[str, Any] | None = None,
    pip_size: float = 0.01,
    timezone_name: str = "Europe/Isle_of_Man",
    broker_state: dict[str, Any] | None = None,
) -> discord.Embed:
    if broker_state is not None:
        return mt5_signal_embed(signal, broker_state)
    direction = str(signal.get("direction") or "?")
    symbol = str(signal.get("symbol") or "?")
    provider = _provider_name(signal)
    status = str(signal.get("status") or "UNKNOWN")

    low = _price(signal.get("entry_low"))
    high = _price(signal.get("entry_high"))
    entry = low if low == high else f"{low} - {high}"

    is_test = bool(signal.get("is_test"))
    embed = discord.Embed(
        title=f"{'TEST | ' if is_test else ''}{direction} {symbol}",
        description=(
            f"**Internal test signal**\nSource: **{provider}**"
            if is_test
            else f"**Internal parsed signal**\nSource: **{provider}**"
        ),
    )
    embed.add_field(name="Entry", value=entry, inline=True)

    if signal.get("sl_mode") == "BREAKEVEN":
        sl_text = "BE"
    elif signal.get("sl_mode") == "UNSET":
        sl_text = "Not set"
    else:
        sl_text = _price(signal.get("current_sl"))
    embed.add_field(name="Stop Loss", value=sl_text, inline=True)
    embed.add_field(name="Status", value=status, inline=True)

    if symbol == "XAUUSD":
        if spot_quote is not None and spot_quote.get("price") is not None:
            spot_value = f"**{_price(spot_quote['price'])}** USD/oz"
            observed = spot_quote.get("computed_at")
            if observed:
                spot_value += f"\nObserved {_local_time(observed, timezone_name)}"
            if spot_quote.get("is_stale"):
                spot_value += "\nStale quote"
            spot_value += "\n[Data by Standard Bullion](https://standardbullion.com)"
        else:
            spot_value = "Temporarily unavailable"
        delay_seconds = int(spot_quote.get("delay_seconds") or 0) if spot_quote else 0
        field_name = (
            f"XAU/USD Paper Mark ({delay_seconds // 60}m delayed)"
            if delay_seconds > 0
            else "XAU/USD Spot"
        )
        embed.add_field(name=field_name, value=spot_value, inline=False)

        if paper_position is not None:
            metrics = _paper_position_metrics(paper_position, pip_size)
            stop = paper_position.get("stop_loss")
            if stop is None:
                paper_stop = "None"
            else:
                paper_stop = _price(stop)
                if bool(paper_position.get("temporary_stop_active")):
                    paper_stop = f"TEMP {paper_stop}"
                elif str(paper_position.get("stop_source") or "") == "BREAKEVEN":
                    paper_stop = f"BE {paper_stop}"

            initial_qty = float(paper_position.get("initial_quantity_oz") or 0.0)
            remaining_qty = float(paper_position.get("remaining_quantity_oz") or 0.0)
            remaining_pct = (
                remaining_qty / initial_qty * 100.0
                if initial_qty > 0
                else 0.0
            )
            embed.add_field(
                name="Paper Execution",
                value=(
                    f"Fill **{_price(paper_position.get('entry_price'))}** | "
                    f"**{float(paper_position.get('lot_size') or 0.0):.2f} lots**\n"
                    f"Stop **{paper_stop}** | Remaining **{remaining_pct:.1f}%**"
                ),
                inline=False,
            )
            embed.add_field(
                name="Realised PnL",
                value=(
                    f"**{_paper_signed_usd(metrics['realised_usd'])}**\n"
                    f"**{metrics['realised_pips']:+,.1f} pips** | "
                    f"{metrics['realised_r']:+.2f}R"
                ),
                inline=True,
            )
            embed.add_field(
                name="Open PnL",
                value=(
                    f"**{_paper_signed_usd(metrics['open_usd'])}**\n"
                    f"**{metrics['live_pips']:+,.1f} pips** | "
                    f"{metrics['open_r']:+.2f}R"
                ),
                inline=True,
            )
            embed.add_field(
                name="Total PnL",
                value=(
                    f"**{_paper_signed_usd(metrics['total_usd'])}**\n"
                    f"**{metrics['total_weighted_pips']:+,.1f} weighted pips** | "
                    f"{metrics['total_r']:+.2f}R"
                ),
                inline=True,
            )
        else:
            realised_text, floating_text, total_text = _signal_pnl_text(
                signal,
                spot_quote,
                timezone_name,
            )
            signal_pips = None
            if (
                spot_quote is not None
                and spot_quote.get("price") is not None
                and pip_size > 0
            ):
                signal_pips = (
                    _move_points(signal, float(spot_quote["price"])) / pip_size
                )

            embed.add_field(name="Realised PnL", value=realised_text, inline=True)
            embed.add_field(
                name="Open PnL",
                value=(
                    floating_text
                    if signal_pips is None
                    else f"**{signal_pips:+,.1f} pips**\n{floating_text}"
                ),
                inline=True,
            )
            embed.add_field(name="Total PnL", value=total_text, inline=True)

    tps = signal.get("tps") or {}
    hits = {int(value) for value in signal.get("tp_hits") or []}
    if tps:
        tp_lines: list[str] = []
        for raw_index, target in sorted(tps.items(), key=lambda item: int(item[0])):
            index = int(raw_index)
            marker = "✅" if index in hits else "⬜"
            tp_lines.append(f"{marker} TP{index}: {_price(target)}")
        embed.add_field(name="Targets", value="\n".join(tp_lines), inline=False)
    else:
        embed.add_field(name="Take Profit", value="Open", inline=False)

    partials = int(signal.get("partial_close_count", 0) or 0)
    if partials:
        remaining = float(signal.get("remaining_fraction", 1.0) or 0.0) * 100
        embed.add_field(
            name="Position Management",
            value=f"Partials taken: {partials}\nRemaining: {remaining:.2f}%",
            inline=False,
        )

    history_lines, _, _, _ = _history_summary(signal, timezone_name)
    if history_lines:
        history_text = "\n".join(history_lines)
        if len(history_text) > 1024:
            history_text = history_text[-1024:]
            newline = history_text.find("\n")
            if newline >= 0:
                history_text = history_text[newline + 1 :]
        embed.add_field(
            name="Trade History",
            value=history_text,
            inline=False,
        )

    extra: list[str] = []
    layers = int(signal.get("layers", 1) or 1)
    reentries = int(signal.get("reentries", 0) or 0)
    if layers > 1:
        extra.append(f"Layers: {layers}")
    if reentries:
        extra.append(f"Re-entries: {reentries}")
    if signal.get("layer_max") is not None:
        extra.append(f"Layer max: {_price(signal.get('layer_max'))}")
    if extra:
        embed.add_field(name="Setup", value="\n".join(extra), inline=False)

    opened_at = signal.get("opened_at")
    root_message_id = signal.get("root_message_id")
    footer = "INTERNAL ONLY"
    if root_message_id is not None:
        footer += f" | Telegram message {root_message_id}"
    if opened_at:
        footer += f" | Opened {_local_timestamp(opened_at, timezone_name)}"
    embed.set_footer(text=footer)
    return embed


def _mt5_account1_embed(snapshot: dict[str, Any], *, pip_size: float,
                        telegram_healthy: bool | None = None,
                        processor_healthy: bool | None = None,
                        weekly_pip_history: dict[str, Any] | None = None) -> discord.Embed:
    """Broker-based Account 1 view; never source/provider paper statistics."""
    summary = summarize(snapshot, pip_size=pip_size)
    stale = summary["stale"]
    def dot(value: float) -> str:
        return "🟢" if value > 0 else ("🔴" if value < 0 else "⚪")
    status = "STALE / NOT LIVE" if stale else "DEMO | MT5 broker data"
    embed = discord.Embed(
        title="DK Capital | MT5 Demo Account 1",
        description=(
            f"**{status}** | Login `{summary['login']}`\n"
            f"Last received: {summary['received_at']}\n"
            f"XAUUSD pip convention: USD {pip_size:.2f} per pip (provisional)"
        ),
        color=discord.Color.orange() if stale else discord.Color.blue(),
    )
    def health(value: bool | None, name: str) -> str:
        return ("🟢" if value is True else ("🔴" if value is False else "⚪")) + " " + name + ": " + (
            "Healthy" if value is True else ("Unavailable / stale" if value is False else "Not checked")
        )
    embed.add_field(
        name="Execution status",
        value=("🟢 MT5 telemetry fresh" if not stale else "🔴 MT5 telemetry stale") +
              "\n" + ("🟢 EA ARMED (DEMO)" if summary.get("entry_enabled") else "🔴 EA DISABLED") +
              "\n" + ("🟢 Instruction feed connected" if summary.get("feed_http") == 200 else "🔴 Instruction feed disconnected") +
              "\n" + health(telegram_healthy, "Telegram listener") +
              "\n" + health(processor_healthy, "Signal processor") +
              "\n" + ("🟠 Daily 2% equity warning (trading still enabled)" if snapshot.get("daily_loss_warning")
                       else "⚪ Daily 2% warning monitor: alert only" if snapshot.get("daily_loss_guard_mode") == "ALERT_ONLY"
                       else "⚪ Daily loss warning: legacy EA telemetry") +
              "\n" + ("🟢 Layer entries enabled" if snapshot.get("layer_entries_enabled")
                       else "⚪ Layer entries disabled"),
        inline=False,
    )
    prefix = "Last known (stale) " if stale else ""
    embed.add_field(
        name=prefix+"Account",
        value=(
            f"Balance: **£{summary['balance']:,.2f}**\n"
            f"Equity: **£{summary['equity']:,.2f}**\n"
            f"Floating P&L: {dot(summary['floating_gbp'])} **£{summary['floating_gbp']:+,.2f}**\n"
            f"Free margin: £{summary['free_margin']:,.2f}"
        ), inline=False,
    )
    embed.add_field(
        name=prefix+"Realised performance (broker deals)",
        value=(
            f"Today: {dot(summary['today_realized_gbp'])} **£{summary['today_realized_gbp']:+,.2f}**\n"
            f"This week: {dot(summary['week_realized_gbp'])} **£{summary['week_realized_gbp']:+,.2f}**\n"
            f"This week (signal pips): {dot(summary['week_realized_pips'])} **{summary['week_realized_pips']:+,.1f} pips** "
            "(each signal counted once)\n"
            f"Lot-weighted average move: {dot(summary['week_lot_weighted_pips'])} "
            f"**{summary['week_lot_weighted_pips']:+,.1f} pips** "
            "(closed volume, before commission/swap)\n"
            + (
                (f"Average weekly: **{weekly_pip_history['average_pips']:+,.1f} pips** "
                 f"(since {weekly_pip_history['first_trade']}, "
                 f"{weekly_pip_history['weeks']} calendar weeks including this week)\n"
                 + ("History feed capped at 1,000 deals: average may omit some trades.\n"
                    if weekly_pip_history.get("source_capped") else ""))
                if weekly_pip_history is not None else
                "Average weekly: **unavailable** (no verified archive)\n"
              )
            + f"Signals with MT5 exits this week: {summary['week_closed_signals']}\n"
            f"Closing deals this week: {summary['week_closed_legs']}"
        ), inline=False,
    )
    if summary["risk_unknown"]:
        risk_text = "**UNKNOWN — inspect protective stops**"
    else:
        risk_text = f"**£{summary['portfolio_risk_gbp']:,.2f} / £150.00**"
    groups = len({p["group"] for p in summary["positions"]})
    remaining = summary["portfolio_risk_gbp"]
    available = "UNKNOWN" if remaining is None else f"£{max(0.0, 150.0 - remaining):,.2f}"
    risk_limit_text = (f"4% maximum planned per signal at provider SL (current cap £{float(snapshot['signal_risk_cap_gbp']):,.2f})"
                       if snapshot.get("signal_risk_cap_pct") == 4.0 and snapshot.get("signal_risk_cap_gbp") is not None
                       else f"Configured cap: {snapshot.get('signal_risk_cap_pct', 'unreported')}%"
                       if snapshot.get("signal_risk_cap_pct") is not None
                       else "Per-signal risk cap not reported by this EA")
    embed.add_field(
        name="Risk and exposure",
        value=f"Per-signal stop-risk: **{risk_limit_text}**\n"
              f"Planned stop-loss exposure: {risk_text}\n"
              f"Remaining portfolio risk capacity: **{available}**\n"
              f"Open signal groups: **{groups}**\n"
              f"Open broker tickets: **{len(summary['positions'])}**",
        inline=False,
    )
    positions = summary["positions"]
    if not positions:
        embed.add_field(name="Open broker positions", value="⚪ None (flat)", inline=False)
    else:
        grouped: dict[int, list[dict[str, Any]]] = {}
        for p in positions:
            grouped.setdefault(p["group"], []).append(p)
        for group, legs in list(grouped.items())[:14]:
            details = []
            for p in sorted(legs, key=lambda x: x["leg"]):
                details.append(
                    f"{dot(p['profit'])} TP{p['leg']} • {p['lots']:.2f} lot • "
                    f"entry {p['entry']:.2f} • SL {p['sl']:.2f} • "
                    f"TP {p['tp']:.2f} • **{p['floating_pips']:+,.1f} pips** • "
                    f"£{p['profit']:+,.2f}"
                )
            embed.add_field(
                name=f"XAUUSD {legs[0]['side']} | Group {group}",
                value="\n".join(details)[:1024],
                inline=False,
            )
        if len(grouped)>14:
            embed.add_field(name="More groups",value=f"+{len(grouped)-14} not shown",inline=False)
    embed.set_footer(text="DEMO ACCOUNT 1 | MT5 fills & deals only; provider claims excluded")
    return embed


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    mt5_channel_raw = os.getenv("MT5_DASHBOARD_CHANNEL_ID", "").strip()
    mt5_alert_raw = os.getenv("MT5_ALERT_CHANNEL_ID", "").strip()
    mt5_results_channel_id = int(os.getenv("MT5_RESULTS_CHANNEL_ID", "1561095999139877046"))
    mt5_results_state_path = Path("/app/data/mt5-results-published.json")
    mt5_changes_channel_id = int(os.getenv("MT5_CHANGES_CHANNEL_ID", "1561099532467445961"))
    mt5_tp_sl_channel_id = int(os.getenv("MT5_TP_SL_CHANNEL_ID", "1561099506337063073"))
    mt5_events_state_path = Path("/app/data/mt5-broker-events-state.json")
    mt5_channel_id = int(mt5_channel_raw) if mt5_channel_raw else 0
    mt5_alert_id = int(mt5_alert_raw) if mt5_alert_raw else 0
    mt5_pip_size = float(os.getenv("MT5_XAU_PIP_SIZE", "0.01"))
    mt5_snapshot = Path(os.getenv("MT5_ACCOUNT1_TELEMETRY_PATH", "/app/data/mt5-account1.json"))
    mt5_dashboard_config = Path("/app/data/mt5-dashboard-config.json")
    mt5_pip_history_path = Path(os.getenv("MT5_ACCOUNT1_PIP_HISTORY_PATH", "/app/data/mt5-account1-pip-history.json"))
    mt5_alert_state_path = Path("/app/data/mt5-alert-state.json")
    telegram_recovery_status_path = Path("/app/data/telegram-recovery-status.json")
    weekly_channel_id = int(os.getenv("DISCORD_WEEKLY_RECORD_CHANNEL_ID", "0"))
    weekly_state_path = Path(os.getenv("WEEKLY_RECORD_STATE_PATH", "/app/data/weekly-record-state.json"))
    telegram_mirror_channel_id = chats_channel_id()
    telegram_mirror_state_path = Path(os.getenv("TELEGRAM_MIRROR_STATE_PATH", "/app/data/telegram-mirror-state.json"))

    async def update_mt5_account1() -> None:
        if not mt5_channel_id and not mt5_alert_id:
            return
        if bot.is_ready():
            _write_json(Path("/app/data/discord-heartbeat.json"), {
                "checked_at": datetime.now(UTC).isoformat(),
                "connected": True,
            })
        snapshot = _load_json(mt5_snapshot)
        problems: set[str] = set()
        summary: dict[str, Any] | None = None
        if not snapshot:
            problems.add("MT5_DEMO_TELEMETRY_MISSING")
        else:
            try:
                summary = summarize(snapshot, pip_size=mt5_pip_size)
            except (ValueError, TypeError, KeyError):
                problems.add("MT5_DEMO_TELEMETRY_MALFORMED")
            else:
                if summary["stale"]:
                    problems.add("MT5_DEMO_TELEMETRY_STALE")
                if summary["risk_unknown"]:
                    problems.add("MT5_DEMO_NATIVE_STOP_OR_RISK_UNKNOWN")
                if summary.get("feed_http") != 200:
                    problems.add("MT5_DEMO_INSTRUCTION_FEED_UNAVAILABLE")
                if any(risk > 75.01 for risk in summary.get("group_risks", {}).values()):
                    problems.add("MT5_DEMO_GROUP_STOP_RISK_OVER_75")
                if summary["portfolio_risk_gbp"] is not None and summary["portfolio_risk_gbp"]>150.01:
                    problems.add("MT5_DEMO_AGGREGATE_STOP_RISK_OVER_150")

        recovery = _load_json(telegram_recovery_status_path)
        if not recovery:
            problems.add("TELEGRAM_RECOVERY_NOT_INITIALIZED")
        else:
            checked = _local_datetime(recovery.get("checked_at"), "UTC")
            if checked is None or (datetime.now(UTC) - checked).total_seconds() > 180:
                problems.add("TELEGRAM_RECOVERY_HEARTBEAT_STALE")
            elif not recovery.get("healthy"):
                problems.add("TELEGRAM_RECOVERY_SCAN_INCOMPLETE")

        if mt5_channel_id and snapshot and summary:
            config = _load_json(mt5_dashboard_config)
            recovery_state = _load_json(telegram_recovery_status_path)
            recovery_stamp = _local_datetime(recovery_state.get("checked_at"), "UTC")
            telegram_ok = (
                recovery_stamp is not None
                and (datetime.now(UTC) - recovery_stamp).total_seconds() <= 180
                and recovery_state.get("healthy") is True
            )
            processor_state = _load_json(Path("/app/data/signal-processor-heartbeat.json"))
            processor_stamp = _local_datetime(processor_state.get("checked_at"), "UTC")
            processor_ok = (
                processor_stamp is not None
                and (datetime.now(UTC) - processor_stamp).total_seconds() <= 90
            )
            try:
                weekly_pip_history = record_weekly_average(
                    mt5_pip_history_path, snapshot, pip_size=mt5_pip_size,
                )
            except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError):
                logger.exception("Unable to update MT5 broker-deal history")
                weekly_pip_history = None
            embed = _mt5_account1_embed(
                snapshot, pip_size=mt5_pip_size,
                telegram_healthy=telegram_ok, processor_healthy=processor_ok,
                weekly_pip_history=weekly_pip_history,
            )
            try:
                channel = bot.get_channel(mt5_channel_id) or await bot.fetch_channel(mt5_channel_id)
                message_id = config.get("message_id")
                if message_id:
                    try:
                        existing = await channel.fetch_message(int(message_id))
                        await existing.edit(embed=embed)
                    except discord.NotFound:
                        message_id = None
                if not message_id:
                    posted = await channel.send(embed=embed)
                    try:
                        await posted.pin(reason="DKcapital MT5 Account 1 dashboard")
                    except (discord.Forbidden, discord.HTTPException):
                        logger.warning("Unable to pin MT5 dashboard")
                    _write_json(mt5_dashboard_config,{"channel_id":mt5_channel_id,"message_id":posted.id})
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception("Unable to publish MT5 Account 1 dashboard")

        if mt5_alert_id:
            prev = _load_json(mt5_alert_state_path)
            old = set(prev.get("active") or [])
            to_send = sorted(problems - old)
            recovered = sorted(old - problems)
            if to_send or recovered:
                try:
                    channel = bot.get_channel(mt5_alert_id) or await bot.fetch_channel(mt5_alert_id)
                    for issue in to_send:
                        await channel.send(f"🚨 DKcapital Account 1 DEMO alert: `{issue}` — investigate; new entries may be blocked by safety checks.")
                    for issue in recovered:
                        await channel.send(f"✅ DKcapital Account 1 DEMO recovery: `{issue}` cleared.")
                    _write_json(mt5_alert_state_path,{
                        "active": sorted(problems),
                        "checked_at": datetime.now(UTC).isoformat(),
                    })
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    logger.exception("Unable to deliver MT5 Account 1 alert")

    async def publish_mt5_results() -> None:
        """Publish once per fully broker-closed trade, never Telegram-only closures."""
        if not mt5_results_channel_id:
            return
        snapshot = _load_json(mt5_snapshot)
        if not verified_snapshot(snapshot):
            return
        signals = _load_json(settings.signal_state_path).get("signals") or []
        if not isinstance(signals, list):
            return
        results = completed_results(snapshot, signals)
        state = _load_json(mt5_results_state_path)
        if not state.get("initialized"):
            # No historical result spam at deployment. Still follow groups with
            # positions open now and publish after their broker-confirmed exit.
            active = set()
            for position in snapshot.get("positions") or []:
                group = position.get("group")
                if group is not None:
                    active.add(int(group))
            state = {
                "initialized": True,
                "cutover": datetime.now(UTC).isoformat(),
                "published": {key: "pre-cutover" for key in results},
                "active_mt5_groups": sorted(active),
            }
            _write_json(mt5_results_state_path, state)
            return
        published = state.setdefault("published", {})
        active = set(state.get("active_mt5_groups") or [])
        for pos in snapshot.get("positions") or []:
            if pos.get("group") is not None:
                active.add(int(pos["group"]))
        state["active_mt5_groups"] = sorted(active)
        cutover = datetime.fromisoformat(state["cutover"])
        try:
            channel = bot.get_channel(mt5_results_channel_id) or await bot.fetch_channel(mt5_results_channel_id)
            for root, result in sorted(results.items(), key=lambda item: item[1]["closed_at"]):
                if root in published:
                    continue
                group_numbers = set()
                for signal in signals:
                    if not isinstance(signal, dict):
                        continue
                    if str(signal.get("group_id") or signal.get("signal_id")) != root:
                        continue
                    group_numbers.add(mt5_group(str(signal.get("signal_id"))))
                if (_local_datetime(result["closed_at"], "UTC") <= cutover
                        and not group_numbers.intersection(active)):
                    published[root] = "pre-cutover"
                    _write_json(mt5_results_state_path, state)
                    continue
                # Persist a stable publication intent to avoid duplicate cards on
                # concurrent loop executions. Delivery errors can be retried.
                msg = await channel.send(embed=result_embed(result))
                published[root] = str(msg.id)
                active.difference_update(group_numbers)
                state["active_mt5_groups"] = sorted(active)
                _write_json(mt5_results_state_path, state)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("MT5 results publication failed channel=%s", mt5_results_channel_id)

    async def publish_mt5_broker_events() -> None:
        """Durably queue real MT5 broker transactions and position modifications."""
        if not mt5_changes_channel_id and not mt5_tp_sl_channel_id:
            return
        snapshot = _load_json(mt5_snapshot)
        if not verified_snapshot(snapshot):
            return
        signals = _load_json(settings.signal_state_path).get("signals") or []
        if not isinstance(signals, list):
            return
        previous = _load_json(mt5_events_state_path)
        updated, discovered = discover_events(previous, snapshot, signals)
        queued = list(previous.get("pending") or [])
        updated["pending"] = queued + discovered
        # Save the queue BEFORE sending to Discord so delivery failures retry.
        _write_json(mt5_events_state_path, updated)
        for event in list(updated["pending"]):
            channel_id = (mt5_tp_sl_channel_id if event.get("channel") == "tp_sl"
                          else mt5_changes_channel_id)
            if not channel_id:
                logger.warning("MT5 notification channel not configured event=%s", event["id"])
                return
            try:
                channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
                await channel.send(embed=notification_embed(event))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                logger.exception("MT5 broker notification failed event=%s", event["id"])
                return
            updated["pending"] = [e for e in updated["pending"] if e["id"] != event["id"]]
            _write_json(mt5_events_state_path, updated)

    async def update_paper_dashboard() -> bool:
        state = _load_json(settings.paper_state_path)
        if not state:
            return False

        channel_id = settings.paper_dashboard_channel_id
        config = _load_json(settings.paper_dashboard_state_path)
        message_id = config.get("message_id")

        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception(
                "Unable to access paper dashboard channel %s",
                channel_id,
            )
            return False

        if not hasattr(channel, "send"):
            logger.error(
                "Configured paper dashboard channel %s is not messageable",
                channel_id,
            )
            return False

        embed = _paper_dashboard_embed(
            state,
            timezone_name=settings.display_timezone,
            pip_size=settings.paper_xau_pip_size,
            risk_pct=settings.paper_risk_pct,
            direction_risk_cap_pct=settings.paper_direction_risk_cap_pct,
            daily_loss_pct=settings.paper_daily_loss_pct,
        )

        try:
            if message_id:
                try:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(embed=embed)
                    return True
                except discord.NotFound:
                    logger.warning(
                        "Paper dashboard message %s was deleted; recreating",
                        message_id,
                    )

            message = await channel.send(embed=embed)
            try:
                await message.pin(
                    reason="Permanent DK Capital paper-trading dashboard",
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.warning(
                    "Paper dashboard created but could not be pinned channel=%s message=%s",
                    channel_id,
                    message.id,
                )

            _write_json(
                settings.paper_dashboard_state_path,
                {
                    "channel_id": channel_id,
                    "message_id": message.id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            logger.info(
                "Paper dashboard created channel=%s message=%s",
                channel_id,
                message.id,
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Paper dashboard update failed")
            return False

    async def update_dashboard() -> bool:
        config = _load_json(settings.discord_dashboard_config_path)
        channel_id = config.get("channel_id")
        message_id = config.get("message_id")
        if not channel_id or not message_id:
            return False

        state = _load_json(settings.signal_state_path)
        if not state:
            return False

        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            message = await channel.fetch_message(int(message_id))
            await message.edit(embed=_dashboard_embed(state))
            return True
        except discord.NotFound:
            logger.warning("Configured legacy dashboard message no longer exists; clearing reference")
            _write_json(settings.discord_dashboard_config_path, {})
        except discord.Forbidden:
            logger.exception("Discord bot cannot edit the configured dashboard")
        except discord.HTTPException:
            logger.exception("Discord dashboard update failed")
        return False

    async def sync_internal_signals() -> None:
        state = _load_json(settings.signal_state_path)
        signals = [s for s in (state.get("signals") or [])
                   if str(s.get("symbol") or "").upper() == "XAUUSD"]
        broker_snapshot = _load_json(mt5_snapshot)
        registry = _load_json(settings.discord_internal_signals_state_path)
        if not registry.get("initialized"):
            _write_json(settings.discord_internal_signals_state_path, {
                "initialized": True, "watermark": datetime.now(UTC).isoformat(),
                "messages": {},
            })
            return
        try:
            channel = bot.get_channel(settings.discord_internal_signals_channel_id) or await bot.fetch_channel(settings.discord_internal_signals_channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Unable to access MT5 signal channel")
            return
        messages = registry.setdefault("messages", {})
        # Fixed cutover prevents historical backlog on upgrade or restart.
        if not registry.get("replay_cutover_at"):
            registry["replay_cutover_at"] = datetime.now(UTC).isoformat()
            _write_json(settings.discord_internal_signals_state_path, registry)
        cutover = str(registry["replay_cutover_at"])
        changed = False
        updates = 0
        # A card manually removed from Discord must not be recreated by a later
        # edit of the same Telegram signal.
        suppressed = registry.setdefault("suppressed", {})
        max_updates_per_cycle = 3  # Keep well below Discord message-edit rate limits.
        # Send newly parsed signals before routine edits to existing cards.
        # Otherwise three busy older cards can consume every update slot.
        for signal in _ordered_signals_for_discord(signals, messages):
            signal_id = str(signal.get("signal_id") or "")
            if not signal_id:
                continue
            prior = messages.get(signal_id)
            if not prior and (
                signal_id in suppressed or not _new_signal_card_eligible(signal, cutover)
            ):
                continue
            if isinstance(prior, int):
                prior = {"message_id": prior}
            prior = prior if isinstance(prior, dict) else {}
            broker_state = broker_signal_state(signal_id, broker_snapshot)
            fingerprint = json.dumps({
                "direction": signal.get("direction"),
                "entry_low": signal.get("entry_low"),
                "entry_high": signal.get("entry_high"),
                "tps": signal.get("tps"),
                "source_style": signal.get("source_style"),
                "current_sl": signal.get("current_sl"),
                "sl_mode": signal.get("sl_mode"),
                "layers": signal.get("layers"),
                "broker": broker_state,
            }, sort_keys=True, default=str)
            if prior.get("message_id") and prior.get("fingerprint") == fingerprint:
                continue
            if updates >= max_updates_per_cycle:
                break
            try:
                if prior.get("message_id"):
                    try:
                        msg = await channel.fetch_message(int(prior["message_id"]))
                        await msg.edit(embed=mt5_signal_embed(signal, broker_state))
                    except discord.NotFound:
                        # Keep a durable tombstone so later provider edits
                        # cannot resurrect a manually deleted Discord card.
                        suppressed[signal_id] = datetime.now(UTC).isoformat()
                        messages.pop(signal_id, None)
                        _write_json(settings.discord_internal_signals_state_path, registry)
                        continue
                else:
                    msg = await channel.send(embed=mt5_signal_embed(signal, broker_state))
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("MT5 signal card sync failed signal=%s", signal_id)
                break
            messages[signal_id] = {"message_id": msg.id, "fingerprint": fingerprint}
            changed = True
            updates += 1
            _write_json(settings.discord_internal_signals_state_path, registry)
        if changed:
            logger.info("MT5 broker signal cards synchronized")

    async def update_weekly_record() -> None:
        if not weekly_channel_id:
            return
        snapshot = _load_json(mt5_snapshot)
        if not snapshot:
            return
        now = datetime.now(UTC)
        state = _load_json(weekly_state_path)
        close = close_time(now)
        week_key = close.astimezone(ZoneInfo("Europe/Isle_of_Man")).strftime("%Y-%m-%d")
        # Remember the opening observation for the next week's reporting;
        # do not claim a fabricated starting balance if it was missed.
        current_monday = (now.astimezone(ZoneInfo("Europe/Isle_of_Man")) -
                          __import__("datetime").timedelta(days=now.astimezone(ZoneInfo("Europe/Isle_of_Man")).weekday())).date().isoformat()
        opening = state.setdefault("openings", {})
        if current_monday not in opening:
            opening[current_monday] = float(snapshot.get("balance", 0))
            _write_json(weekly_state_path, state)
        if not record_due(now):
            return
        if week_key in state.get("published", {}):
            return
        monday = (close.astimezone(ZoneInfo("Europe/Isle_of_Man")) -
                  __import__("datetime").timedelta(days=4)).date().isoformat()
        try:
            record = weekly_record(
                snapshot, now=now, pip_size=mt5_pip_size,
                starting_balance=opening.get(monday),
            )
        except (ValueError, TypeError, KeyError):
            logger.warning("Weekly report awaiting broker snapshot near Friday close")
            return
        start = record["starting_balance"]
        start_text = f"£{start:,.2f} (first recorded balance this week)" if start is not None else "Not recorded"
        embed = discord.Embed(
            title=f"DK Capital | Weekly Record | {week_key}",
            description="MT5 broker-confirmed XAUUSD demo performance",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Starting balance", value=start_text, inline=False)
        embed.add_field(name="Ending balance", value=f"£{record['ending_balance']:,.2f}", inline=True)
        embed.add_field(name="Weekly P&L", value=f"£{record['realized_pnl']:+,.2f}", inline=True)
        embed.add_field(name="Weekly pips", value=f"{record['weekly_pips']:+,.1f} (once per signal)", inline=False)
        embed.add_field(name="Trades taken", value=str(record["trades_taken"]), inline=True)
        embed.add_field(name="Signals with exits", value=str(record["closed_signals"]), inline=True)
        embed.set_footer(text="Broker deals only | Friday 17:00 New York market close | GBP")
        channel = bot.get_channel(weekly_channel_id) or await bot.fetch_channel(weekly_channel_id)
        message = await channel.send(embed=embed)
        state.setdefault("published", {})[week_key] = message.id
        _write_json(weekly_state_path, state)

    @tasks.loop(minutes=1)
    async def weekly_record_loop() -> None:
        try:
            await update_weekly_record()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, OSError):
            logger.exception("Weekly record publication failed")

    @weekly_record_loop.before_loop
    async def before_weekly_record_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=5)
    async def telegram_mirror_loop() -> None:
        if telegram_mirror_channel_id:
            try:
                await sync_mirror(
                    bot, channel_id=telegram_mirror_channel_id,
                    event_log=settings.telegram_event_log_path,
                    state_path=telegram_mirror_state_path,
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException, OSError):
                logger.exception("Telegram to Discord mirror failed")

    @telegram_mirror_loop.before_loop
    async def before_telegram_mirror_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def mt5_broker_events_loop() -> None:
        await publish_mt5_broker_events()

    @mt5_broker_events_loop.before_loop
    async def before_mt5_broker_events_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=30)
    async def mt5_results_loop() -> None:
        await publish_mt5_results()

    @mt5_results_loop.before_loop
    async def before_mt5_results_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def internal_signal_loop() -> None:
        await sync_internal_signals()

    @internal_signal_loop.before_loop
    async def before_internal_signal_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def paper_dashboard_loop() -> None:
        await update_paper_dashboard()

    @paper_dashboard_loop.before_loop
    async def before_paper_dashboard_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def mt5_account1_loop() -> None:
        await update_mt5_account1()

    @mt5_account1_loop.before_loop
    async def before_mt5_account1_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def dashboard_loop() -> None:
        await update_dashboard()

    @dashboard_loop.before_loop
    async def before_dashboard_loop() -> None:
        await bot.wait_until_ready()

    @bot.event
    async def on_ready() -> None:
        assert bot.user is not None
        logger.info("Discord connected user=%s user_id=%s", bot.user, bot.user.id)

        if settings.discord_guild_id:
            guild = discord.Object(id=settings.discord_guild_id)
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            logger.info("Synced %s Discord command(s) to guild %s", len(synced), guild.id)
        else:
            synced = await bot.tree.sync()
            logger.info("Synced %s global Discord command(s)", len(synced))

        if (mt5_channel_id or mt5_alert_id) and not mt5_account1_loop.is_running():
            mt5_account1_loop.start()
        # Legacy signal and paper dashboards are intentionally retired.
        # MT5 Account 1 and its broker-confirmed signal cards remain active.
        if not internal_signal_loop.is_running():
            internal_signal_loop.start()
        if mt5_results_channel_id and not mt5_results_loop.is_running():
            mt5_results_loop.start()
        if (mt5_changes_channel_id or mt5_tp_sl_channel_id) and not mt5_broker_events_loop.is_running():
            mt5_broker_events_loop.start()
        if weekly_channel_id and not weekly_record_loop.is_running():
            weekly_record_loop.start()
        if telegram_mirror_channel_id and not telegram_mirror_loop.is_running():
            telegram_mirror_loop.start()

    @bot.tree.command(name="ping", description="Check whether the DK Capital bot is online.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message("DK Capital bot is online.", ephemeral=True)

    @bot.tree.command(
        name="delall",
        description="Delete every message in the current channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def delall(interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Run this command inside a server text channel.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a standard server text channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await channel.purge(
                limit=None,
                bulk=True,
                reason=f"/delall used by {interaction.user} ({interaction.user.id})",
            )
        except discord.Forbidden:
            logger.exception(
                "Discord bot lacks permission to purge channel %s",
                channel.id,
            )
            await interaction.followup.send(
                "I need Manage Messages and Read Message History permissions in this channel.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            logger.exception("Discord /delall failed in channel %s", channel.id)
            await interaction.followup.send(
                "Discord returned an error while deleting the channel messages.",
                ephemeral=True,
            )
            return

        # If this is the internal parsed-signal feed, forget deleted Discord
        # message IDs and move the watermark forward. Otherwise the sync loop
        # would recreate the signal cards that were just purged.
        if channel.id == settings.discord_internal_signals_channel_id:
            _write_json(
                settings.discord_internal_signals_state_path,
                {
                    "initialized": True,
                    "watermark": datetime.now(UTC).isoformat(),
                    "messages": {},
                },
            )

        # If the configured dashboard itself was in this channel, clear its
        # stale message reference so the refresh loop does not keep looking for
        # a message that /delall intentionally removed.
        dashboard = _load_json(settings.discord_dashboard_config_path)
        if str(dashboard.get("channel_id") or "") == str(channel.id):
            _write_json(settings.discord_dashboard_config_path, {})

        # The permanent paper dashboard is deliberately recreated by its loop
        # after a purge. Clear the stale message ID so recreation is immediate.
        if channel.id == settings.paper_dashboard_channel_id:
            _write_json(settings.paper_dashboard_state_path, {})

        logger.warning(
            "Discord /delall channel=%s guild=%s user=%s deleted=%s",
            channel.id,
            interaction.guild.id,
            interaction.user.id,
            len(deleted),
        )
        await interaction.followup.send(
            f"Deleted {len(deleted)} message(s) from #{channel.name}.",
            ephemeral=True,
        )

    @bot.tree.command(
        name="paper_status",
        description="Show the current DK Capital XAUUSD paper account.",
    )
    @app_commands.default_permissions(administrator=True)
    async def paper_status(interaction: discord.Interaction) -> None:
        state = _load_json(settings.paper_state_path)
        if not state:
            await interaction.response.send_message(
                "The paper engine has not written a state snapshot yet.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            embed=_paper_status_embed(
                state,
                timezone_name=settings.display_timezone,
                pip_size=settings.paper_xau_pip_size,
                risk_pct=settings.paper_risk_pct,
                direction_risk_cap_pct=settings.paper_direction_risk_cap_pct,
                daily_loss_pct=settings.paper_daily_loss_pct,
            ),
            ephemeral=True,
        )

    @bot.tree.command(
        name="dashboard_create",
        description="Create the auto-updating DK Capital live trade dashboard in this channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def dashboard_create(interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.channel is None:
            await interaction.response.send_message(
                "Run this command inside the DK Capital server.",
                ephemeral=True,
            )
            return

        state = _load_json(settings.signal_state_path)
        await interaction.response.send_message(embed=_dashboard_embed(state))
        message = await interaction.original_response()

        _write_json(
            settings.discord_dashboard_config_path,
            {
                "guild_id": interaction.guild.id,
                "channel_id": interaction.channel.id,
                "message_id": message.id,
            },
        )
        logger.info(
            "Dashboard configured guild=%s channel=%s message=%s",
            interaction.guild.id,
            interaction.channel.id,
            message.id,
        )


    @bot.tree.command(
        name="test_signal",
        description="Send a test signal to the DK Capital internal signal channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def test_signal(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        channel_id = settings.discord_internal_signals_channel_id
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Unable to access internal signal channel %s", channel_id)
            await interaction.followup.send(
                "I could not access the internal signal channel.",
                ephemeral=True,
            )
            return

        if not hasattr(channel, "send"):
            await interaction.followup.send(
                "The configured internal signal channel is not messageable.",
                ephemeral=True,
            )
            return

        opened_at = datetime.now(UTC).isoformat()
        test = {
            "signal_id": "test:elite:6256",
            "source_style": "elite",
            "direction": "SELL",
            "symbol": "XAUUSD",
            "entry_low": 4157,
            "entry_high": 4157,
            "original_sl": 4164,
            "current_sl": 4164,
            "sl_mode": "PRICE",
            "tps": {},
            "tp_hits": [],
            "layers": 1,
            "reentries": 0,
            "layer_max": None,
            "remaining_fraction": 1.0,
            "partial_close_count": 0,
            "status": "ACTIVE",
            "opened_at": opened_at,
            "root_message_id": "6256 TEST REPLAY",
            "is_test": True,
            "history": [
                {
                    "timestamp": opened_at,
                    "message_id": 6256,
                    "kind": "opened",
                    "entry_low": 4157,
                    "entry_high": 4157,
                    "sl": 4164,
                }
            ],
        }

        try:
            test_spot = _paper_market_quote(
                _load_json(settings.paper_state_path)
            )
            message = await channel.send(
                embed=_signal_embed(
                    test,
                    spot_quote=test_spot,
                    timezone_name=settings.display_timezone,
                )
            )
            await asyncio.sleep(2)

            first_partial_at = datetime.now(UTC).isoformat()
            first_partial_spot = _paper_market_quote(
                _load_json(settings.paper_state_path)
            )
            test["current_sl"] = 4157
            test["partial_close_count"] = 1
            test["remaining_fraction"] = 0.5
            test["last_update_at"] = first_partial_at
            test["history"].extend(
                [
                    {
                        "timestamp": first_partial_at,
                        "message_id": 6257,
                        "kind": "partial_close",
                        "partial_percent": 50.0,
                        "remaining_fraction": 0.5,
                        "market": first_partial_spot,
                    },
                    {
                        "timestamp": first_partial_at,
                        "message_id": 6257,
                        "kind": "trade_update",
                        "sl_before": 4164,
                        "sl_after": 4157,
                        "market": first_partial_spot,
                    },
                ]
            )
            await message.edit(
                embed=_signal_embed(
                    test,
                    spot_quote=first_partial_spot,
                    timezone_name=settings.display_timezone,
                )
            )
            await asyncio.sleep(2)

            second_partial_at = datetime.now(UTC).isoformat()
            second_partial_spot = _paper_market_quote(
                _load_json(settings.paper_state_path)
            )
            test["partial_close_count"] = 2
            test["remaining_fraction"] = 0.25
            test["last_update_at"] = second_partial_at
            test["history"].extend(
                [
                    {
                        "timestamp": second_partial_at,
                        "message_id": 6259,
                        "kind": "partial_close",
                        "partial_percent": 50.0,
                        "remaining_fraction": 0.25,
                        "market": second_partial_spot,
                    },
                    {
                        "timestamp": second_partial_at,
                        "message_id": 6259,
                        "kind": "trade_update",
                        "sl_before": 4157,
                        "sl_after": 4157,
                        "market": second_partial_spot,
                    },
                ]
            )
            await message.edit(
                embed=_signal_embed(
                    test,
                    spot_quote=second_partial_spot,
                    timezone_name=settings.display_timezone,
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            logger.exception("Failed sending internal test signal")
            await interaction.followup.send(
                "The test signal could not be sent.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            (
                f"Elite 6256 test replay completed in <#{channel_id}>. "
                "It includes both partials, SL-to-entry, delayed XAU/USD paper "
                "mark, realised/open/total PnL, trade history, and local time."
            ),
            ephemeral=True,
        )

    @bot.tree.command(
        name="dashboard_refresh",
        description="Force an immediate refresh of the DK Capital dashboard.",
    )
    @app_commands.default_permissions(administrator=True)
    async def dashboard_refresh(interaction: discord.Interaction) -> None:
        updated = await update_dashboard()
        await interaction.response.send_message(
            "Dashboard refreshed." if updated else "No configured dashboard was found.",
            ephemeral=True,
        )

    return bot


def main() -> None:
    settings = Settings.from_env()
    settings.validate_discord()
    configure_logging(settings.log_level)

    bot = build_bot(settings)
    bot.run(settings.discord_bot_token, log_handler=None)


if __name__ == "__main__":
    main()
