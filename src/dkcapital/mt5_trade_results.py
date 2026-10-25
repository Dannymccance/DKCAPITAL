"""MT5-only final results for fully reconciled DK Capital provider trades.

A provider trade may include multiple signal IDs sharing one group_id. Every
number comes from broker IN/OUT deals, never Telegram target-hit claims.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group

EXPECTED_LOGIN = "5057220041"
MANUAL_REASONS = {"CLIENT", "MOBILE", "WEB"}


def _instant(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def verified_snapshot(snapshot: dict[str, Any], *, now: datetime | None = None) -> bool:
    if not isinstance(snapshot, dict):
        return False
    if (snapshot.get("version") != 2 or snapshot.get("mode") != "DEMO"
            or str(snapshot.get("login")) != EXPECTED_LOGIN
            or snapshot.get("currency") != "GBP"
            or not isinstance(snapshot.get("deals"), list)
            or not isinstance(snapshot.get("positions"), list)):
        return False
    stamp = _instant(snapshot.get("received_at"))
    if stamp is None:
        return False
    age = ((now or datetime.now(UTC)) - stamp).total_seconds()
    return -15 <= age <= 120


def completed_results(snapshot: dict[str, Any], signals: list[dict[str, Any]],
                      *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Only complete, flat broker groups, with all opening volume matched by exits.

    Missing entry history, missing closing deals, a still-open leg, or an
    outstanding owned pending order prohibits publication.
    """
    if not verified_snapshot(snapshot, now=now):
        return {}
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    magics: dict[int, tuple[str, str, int]] = {}
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("symbol") != "XAUUSD":
            continue
        sid = str(signal.get("signal_id") or "")
        if not sid:
            continue
        root = str(signal.get("group_id") or sid)
        by_provider[root].append(signal)
        for leg in (1, 2, 3, 4, 5, 6, 7):
            magic = mt5_group(sid) * 10 + leg
            magics[magic] = (root, sid, leg)

    deals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for deal in snapshot["deals"]:
        if not isinstance(deal, dict) or deal.get("entry_type") not in ("IN", "OUT"):
            continue
        match = magics.get(int(deal.get("magic") or 0))
        if match:
            deals[match[0]].append(deal)

    busy: set[str] = set()
    for item in (snapshot.get("positions") or []) + (snapshot.get("orders") or []):
        if not isinstance(item, dict):
            continue
        match = magics.get(int(item.get("magic") or 0))
        if match:
            busy.add(match[0])
    if int(snapshot.get("broker_orders_total") or 0) and "orders" not in snapshot:
        return {}  # Cannot prove a group has no pending broker order.

    results = {}
    for root, group_deals in deals.items():
        if root in busy or not any(d["entry_type"] == "OUT" for d in group_deals):
            continue
        by_position: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for deal in group_deals:
            by_position[int(deal["position_id"])].append(deal)
        if any(
            not any(d["entry_type"] == "IN" for d in position_deals)
            or sum(float(d["lots"]) for d in position_deals if d["entry_type"] == "OUT")
               < sum(float(d["lots"]) for d in position_deals if d["entry_type"] == "IN") - 1e-7
            or sum(float(d["lots"]) for d in position_deals if d["entry_type"] == "OUT")
               > sum(float(d["lots"]) for d in position_deals if d["entry_type"] == "IN") + 1e-7
            for position_deals in by_position.values()
        ):
            continue

        leg_pnl: dict[tuple[str, int], float] = defaultdict(float)
        exit_reasons = []
        last_exit = None
        for deal in group_deals:
            match = magics[int(deal["magic"])]
            key = (match[1], match[2])
            leg_pnl[key] += sum(float(deal.get(k) or 0) for k in ("profit", "commission", "swap"))
            if deal["entry_type"] == "OUT":
                reason = str(deal.get("reason") or "UNVERIFIED").upper()
                exit_reasons.append(reason)
                ts = _instant(deal.get("time"))
                if ts is not None and (last_exit is None or ts > last_exit):
                    last_exit = ts
        if last_exit is None:
            continue
        reasons = set(exit_reasons)
        if reasons and reasons <= MANUAL_REASONS:
            exit_type = "MANUAL BROKER CLOSE"
        elif reasons == {"TP"}:
            exit_type = "BROKER TAKE PROFIT"
        elif reasons == {"SL"}:
            exit_type = "BROKER STOP LOSS"
        elif "UNVERIFIED" in reasons:
            exit_type = "BROKER CLOSED - REASON UNVERIFIED"
        else:
            exit_type = "MIXED BROKER EXITS"

        sorted_signals = sorted(by_provider[root], key=lambda s: str(s.get("opened_at") or ""))
        provider = str(sorted_signals[0].get("source_style") or "telegram").upper()
        # Preserve the exact originating Telegram channel, distinct from provider.
        # Older snapshots might only contain the stable numeric chat identity.
        title = next(
            (str(item.get("source_chat_title")).strip()
             for item in sorted_signals if item.get("source_chat_title")),
            "",
        )
        chat_id = sorted_signals[0].get("chat_id")
        source = title or (f"Telegram chat {chat_id}" if chat_id is not None
                           else "Source not recorded")
        side = str(sorted_signals[0].get("direction") or "?")
        leg_rows = []
        for i, signal in enumerate(sorted_signals, 1):
            sid = str(signal.get("signal_id"))
            for leg in (1, 2, 3, 4, 5, 6, 7):
                if (sid, leg) not in leg_pnl:
                    continue
                name = "Layer (TP2)" if leg == 4 else f"TP{leg - 1 if leg >= 5 else leg} leg"
                if len(sorted_signals) > 1:
                    name += f" (signal {i})"
                leg_rows.append({"label": name, "pnl": round(leg_pnl[(sid, leg)], 2)})
        first_entries = [_instant(d.get("time")) for d in group_deals if d["entry_type"] == "IN"]
        first_entries = [t for t in first_entries if t is not None]
        results[root] = {
            "root": root, "provider": provider, "source": source, "side": side, "legs": leg_rows,
            "pnl": round(sum(leg_pnl.values()), 2), "exit_type": exit_type,
            "closed_at": last_exit.isoformat(),
            "opened_at": min(first_entries).isoformat() if first_entries else None,
            "balance_latest": float(snapshot.get("balance") or 0),
            "currency": snapshot["currency"],
            "deal_tickets": sorted(int(d["ticket"]) for d in group_deals),
        }
    return results


def result_embed(result: dict[str, Any]):
    import discord

    pnl = float(result["pnl"])
    title = "PROFIT" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
    colour = discord.Color.green() if pnl > 0 else discord.Color.red() if pnl < 0 else discord.Color.greyple()
    embed = discord.Embed(title=f"XAUUSD | {title}", description="**FINAL TRADE RESULT**",
                          color=colour)
    embed.add_field(name="PROVIDER", value=result["provider"], inline=True)
    embed.add_field(name="SOURCE", value=str(result.get("source") or "Source not recorded")[:1024], inline=True)
    embed.add_field(name="SIDE", value=result["side"], inline=True)
    embed.add_field(name="FINAL", value="TRADE CLOSED", inline=True)
    sign = "£" if result["currency"] == "GBP" else result["currency"] + " "
    lines = [f"**{item['label']}** {sign}{item['pnl']:+,.2f}"
             for item in result["legs"]]
    embed.add_field(name="MT5 BROKER-CONFIRMED LEG RESULTS",
                    value="\n".join(lines)[:1024] or "No reconciled legs", inline=False)
    embed.add_field(name="TOTAL REALISED P&L", value=f"**{sign}{pnl:+,.2f}**", inline=True)
    embed.add_field(name="EXIT REASON", value=result["exit_type"], inline=True)
    embed.add_field(name="LATEST MT5 BALANCE",
                    value=f"{sign}{result['balance_latest']:,.2f}", inline=True)
    embed.set_footer(text="MT5 closed deals only | Includes broker commissions and swaps | GBP demo account")
    return embed
