"""Single operator-facing MT5 identity for the guarded Dan-only cutover.

Keep internal broker Account 3 identifiers for order history and EA isolation.
Fail closed on any missing/mismatched live broker identity. No credentials here.
"""
from __future__ import annotations

import os
from typing import Any

def dan_only_enabled() -> bool:
    return os.getenv("MT5_DAN_ONLY", "false").strip().lower() in {"true", "1", "yes"}

def live_identity() -> dict[str, Any] | None:
    raw = os.getenv("MT5_ACCOUNT3_LOGIN", "").strip()
    server = os.getenv("MT5_ACCOUNT3_SERVER", "").strip()
    if (not raw.isdigit() or int(raw) <= 0 or not server
            or os.getenv("MT5_ACCOUNT3_MODE", "").strip() != "REAL"
            or os.getenv("MT5_ACCOUNT3_CURRENCY", "").strip() != "USD"):
        return None
    return {"login": int(raw), "mode": "REAL", "currency": "USD", "server": server}

def active_identity() -> dict[str, Any] | None:
    if dan_only_enabled():
        return live_identity()
    return {"login": 5057220041, "mode": "DEMO", "currency": "GBP"}

def matches_active_account(snapshot: dict[str, Any]) -> bool:
    expected = active_identity()
    return (expected is not None and isinstance(snapshot, dict)
            and all(snapshot.get(k) == v for k, v in expected.items()))

def account_label() -> str:
    return "Live Account" if dan_only_enabled() else "Demo Account 1"

def money_sign() -> str:
    return "$" if dan_only_enabled() else "£"
