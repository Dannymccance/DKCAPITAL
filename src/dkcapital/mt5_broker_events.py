"""MT5-only broker notifications, with persistent and replay-safe discovery.

Never treat Telegram TP/SL statements as broker executions. A reason of TP/SL
is shown only when MT5 DEAL_REASON reports it. All other exits go to changes.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group
from dkcapital.mt5_trade_results import verified_snapshot

MAX_SEEN = 6000
MAX_PENDING = 5000


def _lookup(signals: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    mapping = {}
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("symbol") != "XAUUSD":
            continue
        sid = str(signal.get("signal_id") or "")
        if not sid:
            continue
        name = {"gws": "GWS", "elite": "Elite Portfolios"}.get(
            str(signal.get("source_style") or "").lower(), "Telegram provider")
        for leg in (1, 2, 3, 4):
            mapping[mt5_group(sid) * 10 + leg] = {
                "provider": name, "signal_id": sid,
                "leg": "LAYER (TP2)" if leg == 4 else f"TP{leg}",
            }
    return mapping


def _positions(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(int(p["ticket"])): {
        "magic": int(p["magic"]), "sl": float(p.get("sl") or 0),
        "tp": float(p.get("tp") or 0),
        "entry": float(p.get("entry") or 0),
        "lots": float(p.get("lots") or 0),
    } for p in snapshot.get("positions", []) if isinstance(p, dict) and p.get("ticket")}


def _fmt(value: float) -> str:
    return f"£{value:+,.2f}"


def discover_events(
    state: dict[str, Any], snapshot: dict[str, Any],
    signals: list[dict[str, Any]], *, now: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return updated durable state and new events. Pending queue is caller-owned.

    A first run baselines existing MT5 positions/deals, preventing retrospective
    TP/SL announcements and historical notification floods.
    """
    if not verified_snapshot(snapshot, now=now):
        return state, []
    current = _positions(snapshot)
    tickets = [int(d["ticket"]) for d in snapshot["deals"]
               if isinstance(d, dict) and d.get("ticket") is not None]
    new_state = dict(state)
    if not state.get("initialized"):
        new_state.update({
            "initialized": True, "positions": current,
            "seen_deals": tickets[-MAX_SEEN:],
            "last_daily_loss_locked": snapshot.get("daily_loss_locked") is True,
        })
        return new_state, []

    seen = set(int(t) for t in state.get("seen_deals", []))
    known = _lookup(signals)
    pending_ids = {e["id"] for e in state.get("pending", []) if isinstance(e, dict)}
    events = []

    def add(event: dict[str, Any]) -> None:
        if event["id"] not in pending_ids and len(events) < MAX_PENDING:
            events.append(event)
            pending_ids.add(event["id"])

    for d in snapshot["deals"]:
        if not isinstance(d, dict):
            continue
        ticket = int(d.get("ticket") or 0)
        if not ticket or ticket in seen:
            continue
        detail = known.get(int(d.get("magic") or 0), {})
        kind = str(d.get("entry_type") or "")
        reason = str(d.get("reason") or "UNVERIFIED").upper()
        cash = sum(float(d.get(key) or 0) for key in ("profit", "commission", "swap"))
        leg = detail.get("leg", f"Leg {int(d.get('magic') or 0) % 10}")
        provider = detail.get("provider", "Broker account")
        prefix = f"{provider} | {leg} | XAUUSD"
        if kind == "IN":
            status = "LAYER FILLED" if int(d.get("magic") or 0) % 10 == 4 else "ENTRY FILLED"
            add({
                "id": f"deal-{ticket}", "channel": "changes", "title": status,
                "body": f"{prefix}\nMT5 fill: {float(d['lots']):.2f} lots @ {float(d['price']):.2f}",
                "timestamp": d.get("time"),
            })
        elif kind == "OUT":
            if reason in {"TP", "SL"}:
                add({
                    "id": f"deal-{ticket}", "channel": "tp_sl",
                    "title": f"{leg} {'TAKE PROFIT HIT' if reason == 'TP' else 'STOP LOSS HIT'}",
                    "body": f"{prefix}\nBroker closing deal: {_fmt(cash)}"
                            f"\nExit {float(d['lots']):.2f} lots @ {float(d['price']):.2f}"
                            f"\nMT5 reason: {reason}",
                    "timestamp": d.get("time"),
                })
            else:
                add({
                    "id": f"deal-{ticket}", "channel": "changes",
                    "title": ("CRITICAL: BROKER STOP-OUT" if reason == "SO"
                              else "MANUAL MT5 CLOSE / REDUCTION" if reason in {"CLIENT", "WEB", "MOBILE"}
                              else "MT5 POSITION REDUCED / CLOSED"),
                    "body": f"{prefix}\nBroker closing deal: {_fmt(cash)}"
                            f"\nExit {float(d['lots']):.2f} lots @ {float(d['price']):.2f}"
                            f"\nExit reason: {reason} (not claimed as TP/SL)",
                    "timestamp": d.get("time"),
                })
    previous = state.get("positions", {})
    for ticket, current_position in current.items():
        past = previous.get(ticket)
        if not isinstance(past, dict) or int(past.get("magic") or 0) != current_position["magic"]:
            # New broker fills are announced from MT5 IN deals above.
            if current_position["sl"] <= 0:
                add({"id": f"unprotected-{ticket}", "channel": "changes",
                     "title": "CRITICAL: POSITION WITHOUT NATIVE SL",
                     "body": f"MT5 position {ticket} has no broker-native protective stop."})
            continue
        info = known.get(current_position["magic"], {})
        for attr in ("sl", "tp"):
            was, updated = float(past.get(attr) or 0), current_position[attr]
            if abs(updated - was) < 1e-6:
                continue
            add({
                "id": f"{attr}-{ticket}-{updated:.5f}-{snapshot.get('generated_at')}",
                "channel": "changes",
                "title": f"MT5 NATIVE {attr.upper()} CHANGED",
                "body": f"{info.get('provider', 'Broker account')} | {info.get('leg', 'Leg')} "
                        f"| Ticket {ticket}\n{attr.upper()}: {was:.2f} -> {updated:.2f}"
                        + ("\nCRITICAL: protective SL removed" if attr == "sl" and updated <= 0 else ""),
            })
    # Compare requested provider stop to the ACTUAL native MT5 position stop.
    # Notify only if a more protective move is still missing after a grace
    # period. Never demand a provider's request to widen an existing SL.
    watched = set(state.get("warned_provider_stops", []))
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("symbol") != "XAUUSD":
            continue
        sid = str(signal.get("signal_id") or "")
        if not sid:
            continue
        relevant = [pos for pos in current.values()
                    if int(pos["magic"]) // 10 == mt5_group(sid)]
        if not relevant:
            continue
        status = str(signal.get("sl_mode") or "PRICE").upper()
        if status not in ("PRICE", "BREAKEVEN"):
            continue
        updated = str(signal.get("last_update_at") or "")
        try:
            update_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            if update_at.tzinfo is None:
                continue
            if ((now or datetime.now(UTC)) - update_at).total_seconds() < 40:
                continue
        except (ValueError, TypeError):
            continue
        direction = str(signal.get("direction") or "")
        if direction not in ("BUY", "SELL"):
            continue
        expected = float(signal.get("current_sl") or 0)
        for pos in relevant:
            if status == "PRICE" and expected <= 0:
                continue
            threshold = float(pos["entry"]) if status == "BREAKEVEN" else expected
            actual = float(pos["sl"])
            # Only compare improvement. Broker stop that is already more
            # protective than provider's instruction is fully compliant.
            missing = actual <= 0 or (
                actual + 0.000001 < threshold if direction == "BUY"
                else actual - 0.000001 > threshold
            )
            if not missing:
                continue
            key = f"{sid}:{pos['magic']}:{pos['entry']:.5f}:{status}:{threshold:.5f}"
            if key in watched:
                continue
            watched.add(key)
            add({
                "id": "sl-unconfirmed-" + key,
                "channel": "changes",
                "title": "WARNING: PROVIDER SL NOT YET CONFIRMED ON MT5",
                "body": f"Signal {sid} | {direction} XAUUSD\n"
                        f"Expected minimum protective SL: {threshold:.2f}\n"
                        f"Broker SL observed: {actual:.2f}\n"
                        "This is an unconfirmed protection change, not a TP/SL hit.",
            })
    new_state["warned_provider_stops"] = sorted(watched)[-1500:]
    if snapshot.get("daily_loss_locked") is True and not state.get("last_daily_loss_locked"):
        add({
            "id": f"daily-lock-{snapshot.get('daily_loss_day', snapshot.get('generated_at', '')[:10])}",
            "channel": "changes", "title": "DAILY EQUITY LOSS LOCK ACTIVE",
            "body": "MT5 EA reports the daily-loss lock. No new entries or layers permitted."
                    " Existing positions are not liquidated by this safeguard.",
        })
    new_state.update({
        "positions": current,
        "seen_deals": list(dict.fromkeys(list(state.get("seen_deals", [])) + tickets))[-MAX_SEEN:],
        "last_daily_loss_locked": snapshot.get("daily_loss_locked") is True,
    })
    return new_state, events


def notification_embed(event: dict[str, Any]):
    import discord

    critical = "CRITICAL" in event["title"] or "LOSS LOCK" in event["title"]
    color = discord.Color.red() if critical else (
        discord.Color.green() if event["channel"] == "tp_sl" and "TAKE PROFIT" in event["title"]
        else discord.Color.orange() if event["channel"] == "tp_sl" else discord.Color.blue()
    )
    embed = discord.Embed(title=f"DK Capital | {event['title']}",
                          description=event["body"], color=color)
    embed.set_footer(text="Verified MT5 broker telemetry | DEMO Account 1")
    return embed
