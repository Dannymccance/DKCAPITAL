from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging
from dkcapital.market_data import GoldSpotClient

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
        in {"PENDING_STRATEGY", "PENDING_SL"}
    )
    stats = _paper_trade_stats(state, pip_size)

    mode = str(state.get("strategy_mode") or "observe_only")
    entry_policy = str(state.get("entry_policy") or "all_parsed")
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

    mark_text = "Unavailable"
    if mark is not None:
        mark_text = f"**${float(mark):,.2f}**"
        marked_at = state.get("last_mark_at")
        if marked_at:
            mark_text += f"\n{_local_timestamp(marked_at, timezone_name)}"
    embed.add_field(name="XAU/USD Live Mark", value=mark_text, inline=True)

    embed.add_field(
        name="Risk Controls",
        value=(
            f"Per trade: **{risk_pct * 100:.2f}%**\n"
            f"Per direction: **{direction_risk_cap_pct * 100:.2f}%**\n"
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
            f"Cap/book: **${balance * direction_risk_cap_pct:,.2f}**"
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
            f"Queued for strategy: **{queued}**\n"
            f"Pip size: **{pip_size:g}** ($1.00 = {1.0 / pip_size:,.0f} pips)"
        ),
        inline=True,
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
    }
    return json.dumps(relevant, sort_keys=True, separators=(",", ":"))


def _signal_embed(
    signal: dict[str, Any],
    *,
    spot_quote: dict[str, Any] | None = None,
    timezone_name: str = "Europe/Isle_of_Man",
) -> discord.Embed:
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
        else:
            spot_value = "Temporarily unavailable"
        embed.add_field(name="XAU/USD Spot", value=spot_value, inline=False)

        realised_text, floating_text, total_text = _signal_pnl_text(
            signal,
            spot_quote,
            timezone_name,
        )
        embed.add_field(name="Realised PnL", value=realised_text, inline=True)
        embed.add_field(name="Open PnL", value=floating_text, inline=True)
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


def build_bot(settings: Settings) -> commands.Bot:
    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)
    gold_spot = GoldSpotClient(
        settings.gold_spot_url,
        settings.gold_spot_refresh_seconds,
    )

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
            logger.warning("Configured dashboard message no longer exists")
        except discord.Forbidden:
            logger.exception("Discord bot cannot edit the configured dashboard")
        except discord.HTTPException:
            logger.exception("Discord dashboard update failed")
        return False

    async def sync_internal_signals() -> None:
        state = _load_json(settings.signal_state_path)
        signals = list(state.get("signals") or [])
        active_gold = any(
            str(signal.get("symbol") or "") == "XAUUSD"
            and str(signal.get("status") or "") == "ACTIVE"
            for signal in signals
        )
        spot_quote = await gold_spot.quote() if active_gold else None
        registry = _load_json(settings.discord_internal_signals_state_path)

        if not registry.get("initialized"):
            registry = {
                "initialized": True,
                "watermark": datetime.now(UTC).isoformat(),
                "messages": {},
            }
            _write_json(settings.discord_internal_signals_state_path, registry)
            logger.info(
                "Internal signal feed initialized channel=%s watermark=%s",
                settings.discord_internal_signals_channel_id,
                registry["watermark"],
            )
            return

        channel_id = settings.discord_internal_signals_channel_id
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            logger.exception("Unable to access internal signal channel %s", channel_id)
            return

        if not hasattr(channel, "send"):
            logger.error("Configured internal signal channel %s is not messageable", channel_id)
            return

        messages = registry.setdefault("messages", {})
        watermark = str(registry.get("watermark") or "")
        changed = False

        # First update messages we have already published.
        for signal in signals:
            signal_id = str(signal.get("signal_id") or "")
            if not signal_id or signal_id not in messages:
                continue

            record = messages.get(signal_id)
            if isinstance(record, int):
                record = {"message_id": record, "fingerprint": ""}
            elif not isinstance(record, dict):
                record = {}

            message_id = record.get("message_id")
            signal_spot = (
                spot_quote
                if str(signal.get("symbol") or "") == "XAUUSD"
                and str(signal.get("status") or "") == "ACTIVE"
                else None
            )
            fingerprint = _signal_fingerprint(signal, signal_spot)
            if message_id and record.get("fingerprint") == fingerprint:
                continue

            try:
                if message_id:
                    message = await channel.fetch_message(int(message_id))
                    await message.edit(
                        embed=_signal_embed(
                            signal,
                            spot_quote=signal_spot,
                            timezone_name=settings.display_timezone,
                        )
                    )
                else:
                    message = await channel.send(
                        embed=_signal_embed(
                            signal,
                            spot_quote=signal_spot,
                            timezone_name=settings.display_timezone,
                        )
                    )
                messages[signal_id] = {
                    "message_id": message.id,
                    "fingerprint": fingerprint,
                }
                changed = True
            except discord.NotFound:
                message = await channel.send(
                    embed=_signal_embed(
                        signal,
                        spot_quote=signal_spot,
                        timezone_name=settings.display_timezone,
                    )
                )
                messages[signal_id] = {
                    "message_id": message.id,
                    "fingerprint": fingerprint,
                }
                changed = True
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Failed updating internal signal %s", signal_id)

        # Then publish new signals after the persisted watermark. Historical
        # backfill from before the feed was enabled is intentionally not dumped.
        for signal in sorted(signals, key=lambda item: str(item.get("opened_at") or "")):
            signal_id = str(signal.get("signal_id") or "")
            opened_at = str(signal.get("opened_at") or "")
            if not signal_id or signal_id in messages:
                continue
            if not opened_at or (watermark and opened_at <= watermark):
                continue

            signal_spot = (
                spot_quote
                if str(signal.get("symbol") or "") == "XAUUSD"
                and str(signal.get("status") or "") == "ACTIVE"
                else None
            )
            try:
                message = await channel.send(
                    embed=_signal_embed(
                        signal,
                        spot_quote=signal_spot,
                        timezone_name=settings.display_timezone,
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Failed publishing internal signal %s", signal_id)
                break

            messages[signal_id] = {
                "message_id": message.id,
                "fingerprint": _signal_fingerprint(signal, signal_spot),
            }
            watermark = opened_at
            registry["watermark"] = watermark
            changed = True
            logger.info(
                "Published internal signal signal_id=%s channel=%s message=%s",
                signal_id,
                channel_id,
                message.id,
            )

        if changed:
            _write_json(settings.discord_internal_signals_state_path, registry)

    @tasks.loop(seconds=2)
    async def internal_signal_loop() -> None:
        await sync_internal_signals()

    @internal_signal_loop.before_loop
    async def before_internal_signal_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=5)
    async def paper_dashboard_loop() -> None:
        await update_paper_dashboard()

    @paper_dashboard_loop.before_loop
    async def before_paper_dashboard_loop() -> None:
        await bot.wait_until_ready()

    @tasks.loop(seconds=5)
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

        if not dashboard_loop.is_running():
            dashboard_loop.start()
        if not paper_dashboard_loop.is_running():
            paper_dashboard_loop.start()
        if not internal_signal_loop.is_running():
            internal_signal_loop.start()

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
            test_spot = await gold_spot.quote(force=True)
            message = await channel.send(
                embed=_signal_embed(
                    test,
                    spot_quote=test_spot,
                    timezone_name=settings.display_timezone,
                )
            )
            await asyncio.sleep(2)

            first_partial_at = datetime.now(UTC).isoformat()
            first_partial_spot = await gold_spot.quote(force=True)
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
            second_partial_spot = await gold_spot.quote(force=True)
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
                "It includes both partials, SL-to-entry, live XAU/USD spot, "
                "realised/open/total PnL, trade history, and local time."
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
