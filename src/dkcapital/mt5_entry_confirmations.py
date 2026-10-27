"""Independent, broker-verified MT5 entry confirmations for three accounts.

Only authenticated gateway snapshots (login/mode/currency/server/freshness)
can produce checkmarks. A provider signal, EA decision or feed acknowledgement
is NEVER treated as a fill. No order submission or MT5 service control.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dkcapital.mt5_signal_cards import mt5_group
from dkcapital.mt5_active_account import dan_only_enabled, live_identity

MAX_AGE_SECONDS = 150
PENDING_SECONDS = 180

# Account 1 login is the existing strictly-pinned demo installation.
IDENTITIES = {
    1: {"login": 5057220041, "mode": "DEMO", "currency": "GBP"},
    2: {"login": 24038172, "mode": "REAL", "currency": "GBP",
        "server": "VantageMarkets-Live 14"},
    3: {"login": 36193223, "mode": "REAL", "currency": "USD",
        "server": "VantageMarkets-Live 4"},
}
NAMES = {2: "Kyle", 3: "Dan", 1: "Demo"}
ORDER = (2, 3, 1)


def instant(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError, TypeError):
        return None


def signal_is_new(signal: dict, cutover: str) -> bool:
    publish = instant(signal.get("publish_ready_at") or signal.get("opened_at"))
    boundary = instant(cutover)
    return publish is not None and boundary is not None and publish > boundary


def validated(snapshot: dict, account: int, now: datetime) -> bool:
    expected = live_identity() if dan_only_enabled() and account == 3 else IDENTITIES[account]
    if expected is None:
        return False
    if not isinstance(snapshot, dict) or snapshot.get("version") != 2:
        return False
    for field, value in expected.items():
        if snapshot.get(field) != value:
            return False
    stamp = instant(snapshot.get("received_at"))
    if stamp is None or not -15 <= (now - stamp).total_seconds() <= MAX_AGE_SECONDS:
        return False
    if not isinstance(snapshot.get("positions"), list) or not isinstance(snapshot.get("deals"), list):
        return False
    # A partial snapshot (missing broker position inventory) cannot be used
    # to certify that an account has not entered.
    for field in ("broker_positions_total", "broker_orders_total"):
        if type(snapshot.get(field)) is not int or snapshot[field] < 0:
            return False
    return True


def entry_evidence(signal_id: str, snapshot: dict) -> dict[str, Any] | None:
    """Require a native position magic or an actual MT5 DEAL_ENTRY_IN.

    A stale/invalid snapshot must be filtered by validated() before calling.
    One signal's magic must never be confused with another signal or account.
    """
    group = mt5_group(signal_id)
    accepted = {group * 10 + leg for leg in range(1, 8)}
    deals = [
        deal for deal in snapshot["deals"]
        if isinstance(deal, dict)
        and deal.get("entry_type") == "IN"
        and type(deal.get("magic")) is int and deal["magic"] in accepted
        and type(deal.get("ticket")) is int and deal["ticket"] > 0
        and type(deal.get("lots")) in (int, float) and deal["lots"] > 0
    ]
    if deals:
        first = min(deals, key=lambda d: (str(d.get("time") or ""), d["ticket"]))
        return {"proof": "MT5 DEAL_ENTRY_IN", "ticket": first["ticket"]}
    for pos in snapshot["positions"]:
        if (isinstance(pos, dict) and type(pos.get("magic")) is int
                and pos["magic"] in accepted and type(pos.get("ticket")) is int
                and pos["ticket"] > 0
                and type(pos.get("lots")) in (int, float) and pos["lots"] > 0):
            return {"proof": "MT5 OPEN POSITION", "ticket": pos["ticket"]}
    return None


def account_status(signal: dict, account: int, snapshot: dict,
                   prior_proof: dict | None, now: datetime) -> tuple[str, dict | None]:
    if prior_proof and prior_proof.get("proof") in {"MT5 DEAL_ENTRY_IN", "MT5 OPEN POSITION"}:
        # Never erase a previously observed broker fill when the position closes
        # or the EA's finite history rolls off.
        return "✅ Entered (broker-confirmed)", prior_proof
    if not validated(snapshot, account, now):
        return "⚠️ Unknown (MT5 telemetry unverified)", None
    signal_id = str(signal.get("signal_id") or "")
    proof = entry_evidence(signal_id, snapshot) if signal_id else None
    if proof:
        return "✅ Entered (broker-confirmed)", proof
    if snapshot.get("feed_http") != 200:
        return "⚠️ Unknown (instruction feed unavailable)", None
    opened = instant(signal.get("opened_at") or signal.get("publish_ready_at"))
    if opened is None:
        return "⚠️ Unknown (signal time unavailable)", None
    age = (now - opened).total_seconds()
    if age < PENDING_SECONDS:
        return "⏳ Awaiting broker fill", None
    # Absence of a matching deal is not a certified negative: broker history
    # is bounded, and trades could have occurred while telemetry was missing.
    return "❌ No broker entry confirmed", None


def build_confirmation(signal: dict, snapshots: dict[int, dict],
                       proofs: dict[str, dict], now: datetime
                       ) -> tuple[str, dict[str, dict]]:
    """Return Discord message and updated per-signal, per-account evidence."""
    ident = str(signal.get("signal_id") or "")
    # Signal IDs come from Telegram channel/message references, not message text.
    ref = ident.replace("`", "").replace("\n", " ")[:100]
    provider = {"gws": "GWS", "elite": "Elite Portfolios"}.get(
        str(signal.get("source_style") or "").lower(), "Telegram provider")
    direction = str(signal.get("direction") or "?").upper()
    if direction not in ("BUY", "SELL"):
        direction = "?"
    kept = dict(proofs) if isinstance(proofs, dict) else {}
    statuses: list[str] = []
    for account in ((3,) if dan_only_enabled() else ORDER):
        status, evidence = account_status(
            signal, account, snapshots.get(account, {}),
            kept.get(str(account)), now,
        )
        if evidence:
            kept[str(account)] = evidence
        statuses.append(f"**{'Live Account' if dan_only_enabled() else NAMES[account]}:** {status}")
    low, high = signal.get("entry_low"), signal.get("entry_high")
    entry = f"{low}–{high}" if low is not None and high is not None and low != high else str(low or "unknown")
    body = (
        f"**{direction} XAUUSD | {provider}**\n"
        f"**Signal reference:** `{ref}`\n"
        f"**Entry zone:** {entry}\n\n"
        + "\n".join(statuses)
        + "\n\n*✅ = MT5 position or entry deal verified. "
          "❌ = no confirmed fill in available evidence (not proof of rejection). "
          "⚠️ = cannot verify. Updates automatically.*"
    )
    return body, kept
