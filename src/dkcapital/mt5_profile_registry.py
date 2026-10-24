"""Declarative, strictly scoped MT5 monitoring profiles.

Absent registry preserves the historical Account 1 watchdog. Each additional
account is explicitly commissioned; there are no credentials in this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILE_REGISTRY_NAME = "mt5-monitor-profiles.json"
MAX_PROFILES = 20


def profiles_from_registry(data_dir: Path) -> tuple[list[dict[str, Any]], bool]:
    """Return (validated enabled profiles, valid_config).

    Fail closed on malformed profiles; watchdog will alert, not silently omit
    misconfigured accounts. Account 1 is always monitored.
    """
    path = data_dir / PROFILE_REGISTRY_NAME
    default = [{
        "id": 1, "label": "Paper MT5 demo",
        "expected_mode": "DEMO", "expected_currency": "GBP",
        "expected_login": None, "expect_entries": True,
    }]
    if not path.exists():
        return default, True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
            raise ValueError("expected accounts list")
        rows = payload["accounts"]
        if len(rows) > MAX_PROFILES:
            raise ValueError("too many MT5 accounts")
        result, seen = [], set()
        for raw in rows:
            if not isinstance(raw, dict) or type(raw.get("id")) is not int:
                raise ValueError("invalid profile")
            ident = raw["id"]
            if not 1 <= ident <= MAX_PROFILES or ident in seen:
                raise ValueError("profile id out of range or duplicate")
            seen.add(ident)
            if type(raw.get("enabled")) is not bool:
                raise ValueError("enabled must be explicit boolean")
            if not raw["enabled"]:
                if ident == 1:
                    raise ValueError("existing Account 1 must remain monitored")
                continue
            label = raw.get("label")
            if not isinstance(label, str) or not label.strip() or len(label) > 80:
                raise ValueError("invalid label")
            mode = raw.get("expected_mode")
            currency = raw.get("expected_currency")
            if mode not in {"DEMO", "REAL"}:
                raise ValueError("expected mode must be DEMO or REAL")
            if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha():
                raise ValueError("invalid account currency")
            login = raw.get("expected_login")
            if type(login) is not int or login <= 0:
                raise ValueError("expected_login must be a confirmed positive integer")
            if type(raw.get("expect_entries")) is not bool:
                raise ValueError("expect_entries must be explicit boolean")
            if raw["enabled"]:
                result.append({
                    "id": ident, "label": label.strip(),
                    "expected_mode": mode, "expected_currency": currency.upper(),
                    "expected_login": login,
                    "expect_entries": raw["expect_entries"],
                })
        if 1 not in {p["id"] for p in result}:
            raise ValueError("existing Account 1 must remain monitored")
        return result, True
    except (OSError, ValueError, TypeError, OverflowError, KeyError, json.JSONDecodeError):
        return default, False


def telemetry_file(data_dir: Path, ident: int) -> Path:
    return data_dir / f"mt5-account{ident}.json"


def service_unit(ident: int) -> str:
    if type(ident) is not int or not 1 <= ident <= MAX_PROFILES:
        raise ValueError("invalid MT5 account id")
    return f"dkcapital-mt5-{ident}.service"
