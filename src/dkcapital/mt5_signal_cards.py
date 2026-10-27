"""Broker-confirmed display state for Telegram signal cards (demo Account 1).

Matches the same FNV-1a signal ID -> magic values as DKcapitalV2.mq5.
No Telegram TP-hit, status or paper results are interpreted as broker fills.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

MAGIC_BASE = 5100000000


def mt5_group(signal_id: str) -> int:
    hashed = 2166136261
    for char in signal_id:
        hashed = ((hashed ^ ord(char)) * 16777619) & 0xFFFFFFFF
    return (MAGIC_BASE + hashed * 10) // 10


def broker_signal_state(signal_id: str, telemetry: dict[str, Any] | None,
                        *, now: datetime | None = None) -> dict[str, Any]:
    """Return unverified unless recent, account-validated telemetry exists.

    A green check on the Discord card means a confirmed entry fill.
    A confirmed closure is labeled separately, without assuming TP reason.
    """
    empty = {"status": "MT5 telemetry unavailable", "legs": {},
             "has_broker_record": False, "fresh": False}
    if not telemetry or telemetry.get("version") != 2 or telemetry.get("mode") != "DEMO" or str(telemetry.get("currency")) != "GBP" or str(telemetry.get("login")) != "5057220041":
        return empty
    try:
        stamp = datetime.fromisoformat(str(telemetry["received_at"]).replace("Z", "+00:00"))
        now = now or datetime.now(UTC)
        fresh = stamp.tzinfo is not None and -15 <= (now - stamp).total_seconds() <= 120
    except (ValueError, TypeError, KeyError):
        return empty
    if not fresh:
        return {**empty, "status": "MT5 telemetry stale"}
    group = mt5_group(signal_id)
    positions = {}
    by_leg: dict[int, dict[str, Any]] = defaultdict(lambda: {"entries": [], "exits": []})
    for item in telemetry.get("deals", []):
        if not isinstance(item, dict) or item.get("group") != group:
            continue
        leg = int(item.get("magic", 0)) % 10
        if leg not in (1, 2, 3, 4, 5, 6, 7):
            continue
        if item.get("entry_type") == "IN":
            by_leg[leg]["entries"].append(item)
        elif item.get("entry_type") == "OUT":
            by_leg[leg]["exits"].append(item)
    for item in telemetry.get("positions", []):
        if isinstance(item, dict) and item.get("group") == group:
            leg = int(item.get("leg") or 0)
            if leg in (1, 2, 3, 4, 5, 6, 7):
                positions[leg] = item
    legs: dict[int, dict[str, Any]] = {}
    for leg in (1, 2, 3, 4, 5, 6, 7):
        entries = by_leg[leg]["entries"]
        exits = by_leg[leg]["exits"]
        pos = positions.get(leg)
        if not entries and not pos:
            if leg in (1, 2, 3):
                legs[leg] = {"status": "Not filled on MT5", "entry": None}
            continue
        if entries:
            lots = sum(float(d["lots"]) for d in entries)
            entry = sum(float(d["lots"]) * float(d["price"]) for d in entries) / lots if lots else None
            exited_lots = sum(float(d["lots"]) for d in exits)
            initial_lots = lots
        else:
            entry = float(pos["entry"])
            initial_lots = float(pos["lots"])
            exited_lots = 0.0
        if pos:
            leg_status = "OPEN"
        elif exits and exited_lots >= initial_lots - 1e-7:
            leg_status = "CLOSED (MT5 confirmed; reason not verified)"
        elif exits:
            leg_status = "PARTIALLY CLOSED"
        else:
            leg_status = "Fill recorded; position reconciliation pending"
        legs[leg] = {
            "status": leg_status,
            "entry": entry,
            "lots": initial_lots,
            "sl": float(pos["sl"]) if pos else None,
            "tp": float(pos["tp"]) if pos else None,
            "exit_price": (sum(float(d["lots"]) * float(d["price"]) for d in exits) / exited_lots) if exited_lots else None,
            "realized_gbp": sum(float(d["profit"]) + float(d["commission"]) + float(d["swap"]) for d in exits),
        }
    decisions = [d for d in telemetry.get("decisions", []) if isinstance(d, dict)
                 and d.get("signal_id") == signal_id]
    latest_entry = next((d for d in reversed(decisions) if d.get("stage") == "ENTRY"), None)
    latest_layer = next((d for d in reversed(decisions) if d.get("stage") == "LAYER"), None)
    has_record = bool(positions or any(x["entries"] for x in by_leg.values()))
    if not has_record:
        status = "NOT FILLED ON MT5"
    elif any(v["status"] == "OPEN" for v in legs.values()):
        status = "OPEN (MT5 confirmed)"
    elif all(v["status"].startswith("CLOSED") for v in legs.values()
             if v.get("entry") is not None):
        status = "CLOSED (MT5 confirmed)"
    else:
        status = "PARTIAL / RECONCILIATION"
    return {"status": status, "legs": legs, "has_broker_record": has_record, "fresh": True,
            "decision": latest_entry, "layer_decision": latest_layer,
            "broker_entry_leg_count": sum(1 for x in legs.values() if x.get("entry") is not None),
            "broker_history_complete": False}


def mt5_signal_embed(signal: dict[str, Any], state: dict[str, Any]):
    """Gold channel card: provider intent vs actual MT5 fills, clearly separated."""
    import discord

    provider = {"gws": "GWS", "elite": "Elite Portfolios"}.get(
        str(signal.get("source_style") or "").lower(), "Telegram Provider")
    side = str(signal.get("direction") or "?")
    embed = discord.Embed(
        title=f"{side} XAUUSD",
        description=f"**Provider: {provider}**\n**MT5 DEMO ACCOUNT 1:** {state['status']}",
        color=discord.Color.green() if state["has_broker_record"] else discord.Color.orange(),
    )
    low = signal.get("entry_low")
    high = signal.get("entry_high")
    entry = f"{low} - {high}" if low != high else str(low)
    embed.add_field(name="Telegram entry zone", value=entry, inline=True)
    embed.add_field(name="MT5 broker status", value=state["status"], inline=True)
    rows = []
    declared = signal.get("tps") or {}
    num_numeric = len(declared)
    has_runner = bool(signal.get("tp3_open"))
    expected = 3 if has_runner else min(num_numeric, 6)
    for leg in (1, 2, 3, 4, 5, 6, 7):
        # 4 has always been the optional layer; 5 denotes numeric TP4.
        if leg >= 5 and num_numeric < leg - 1 and leg not in state["legs"]:
            continue
        if leg in (1, 2, 3) and leg > expected and leg not in state["legs"]:
            continue
        item = state["legs"].get(leg)
        if leg == 4 and (item is None or item.get("entry") is None):
            continue
        if item is None or item["entry"] is None:
            rows.append(f"⬜ TP{leg - 1 if leg >= 5 else leg} leg — **Not filled on MT5**")
            continue
        status = item["status"]
        mark = "✅"  # Confirmed broker entry fill; closing reason shown separately.
        label = "10% layer (TP2)" if leg == 4 else f"TP{leg - 1 if leg >= 5 else leg} leg"
        line = f"{mark} **{label}** — {item['lots']:.2f} lots, fill **{item['entry']:.2f}**"
        line += f"\n{status}"
        if item.get("exit_price") is not None:
            line += f" | exit {item['exit_price']:.2f}"
        if item.get("sl") is not None:
            line += f" | SL {item['sl']:.2f}"
        if item.get("tp") is not None:
            line += f" | TP {item['tp']:.2f}"
        rows.append(line)
    embed.add_field(name="Broker-confirmed fills (with optional 10% layer)", value="\n".join(rows)[:1024], inline=False)
    if str(signal.get("sl_mode") or "").upper() == "BREAKEVEN":
        embed.add_field(name="Telegram requested SL", value="BREAKEVEN (await MT5 confirmation)", inline=False)
    elif signal.get("current_sl") is not None:
        embed.add_field(name="Telegram requested SL",
                        value=f"{float(signal['current_sl']):.2f} (not a broker confirmation)",
                        inline=False)
    targets = signal.get("tps") or {}
    if targets:
        embed.add_field(
            name="Telegram targets (NOT broker-confirmed)",
            value="\n".join(f"TP{k}: {v}" for k, v in sorted(targets.items(), key=lambda x: int(x[0])))[:1024],
            inline=False,
        )
    if not state["has_broker_record"]:
        decision = state.get("decision") or {}
        code = str(decision.get("code") or "NOT_YET_RECORDED")
        at = str(decision.get("at") or "")
        broker_ask = decision.get("ask")
        broker_bid = decision.get("bid")
        reason = ("Exact MT5 reason not yet recorded" if code == "NOT_YET_RECORDED"
                  else code.replace("_", " ").capitalize())
        previous = str(decision.get("prior_code") or "")
        if previous:
            reason += f" (previous blocker: {previous.replace('_', ' ').lower()})"
        quote = (f"\nBroker BID/ASK: {broker_bid:.2f} / {broker_ask:.2f}"
                 if isinstance(broker_bid, (int, float)) and isinstance(broker_ask, (int, float))
                 and broker_ask > 0 else "")
        embed.add_field(name="Why wasn't this entered?",
                        value=(f"**{reason}** | {at}{quote}" if code != "NOT_YET_RECORDED"
                               else "Not filled on MT5; rejection reason not verified.")[:1024],
                        inline=False)
    if state.get("backend_entry_block"):
        embed.add_field(name="Backend entry restriction",
                        value=str(state["backend_entry_block"]).replace("_", " ")[:1024],
                        inline=False)
    # Show broker entry failures even after a partial fill; they are often
    # the only explanation for incomplete target allocation.
    entry_decision = state.get("decision") or {}
    if state.get("has_broker_record") and str(entry_decision.get("code") or "") in {
        "BROKER_ORDER_REJECTED_OR_PARTIAL", "MINIMUM_LEG_SIZE_OR_MARGIN",
        "BROKER_TP_INVALID", "BROKER_SL_INVALID",
    }:
        embed.add_field(name="MT5 entry diagnostic",
                        value=str(entry_decision.get("code"))[:1024], inline=False)
    layer = state.get("layer_decision")
    if layer and int(signal.get("layers") or 1) > 1:
        embed.add_field(name="MT5 layer decision",
                        value=str(layer.get("code", "UNVERIFIED")).replace("_", " ")[:1024],
                        inline=False)
    embed.set_footer(text="DEMO | ✅ = MT5 ENTRY FILLED | CLOSED does not imply TP | No paper/provider P&L")
    return embed
