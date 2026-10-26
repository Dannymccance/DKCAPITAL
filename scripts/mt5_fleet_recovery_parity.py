#!/usr/bin/env python3
"""Read-only parity assessment: systemd, boot, feed and MT5 recovery policy.

No broker orders, service restarts or enables. Broker-specific identity and
execution symbol are explicitly not expected to match across accounts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_autoheal import discover_data_dir


def _show(unit: str) -> dict[str, Any]:
    try:
        output = subprocess.run(
            ["systemctl", "show", unit, "--no-pager",
             "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState",
             "-p", "Restart", "-p", "RestartUSec", "-p", "RestartPreventExitStatus",
             "-p", "NRestarts"],
            capture_output=True, text=True, timeout=6, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"active": False, "enabled": False, "restart": "unknown"}
    settings = dict(
        line.split("=", 1) for line in output.stdout.splitlines()
        if "=" in line
    )
    return {
        "loaded": settings.get("LoadState") == "loaded",
        "active": settings.get("ActiveState") == "active",
        "enabled": settings.get("UnitFileState") == "enabled",
        "restart": settings.get("Restart", "unknown"),
        "restart_delay": settings.get("RestartUSec", "unknown"),
        "restart_blocked_exit": settings.get("RestartPreventExitStatus", ""),
        "restart_count": settings.get("NRestarts", "unknown"),
    }


def _load(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def snapshot_health(s: dict[str, Any], now: datetime) -> dict[str, Any]:
    try:
        stamp = datetime.fromisoformat(str(s["received_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("no timezone")
        age = (now - stamp.astimezone(UTC)).total_seconds()
    except (ValueError, TypeError, KeyError, OverflowError):
        age = float("inf")
    return {
        "authenticated_recent": 0 <= age <= 150,
        "feed_ok": s.get("feed_http") == 200,
        "entries": s.get("entry_enabled"),
        "broker_stops_protected": s.get("broker_positions_protected") is True,
        "live_reconciliation_supported": s.get("restart_reconciliation_supported") is True,
    }


def parity_report(data_dir: Path, now: datetime) -> dict[str, Any]:
    accounts: dict[str, Any] = {}
    for ident in (1, 2, 3):
        mt5 = _show(f"dkcapital-mt5-{ident}.service")
        vnc = _show(f"dkcapital-vnc{ident}.service")
        snapshot = _load(data_dir / f"mt5-account{ident}.json")
        health = snapshot_health(snapshot, now)
        accounts[str(ident)] = {
            "terminal": mt5, "vnc": vnc, "broker": {
                "mode": snapshot.get("mode"),
                "currency": snapshot.get("currency"),
                "login_verified_in_telemetry": isinstance(snapshot.get("login"), int),
            },
            "telemetry": health,
            "crash_recovery_configured":
                mt5.get("restart") == "always",
            "boot_recovery_configured":
                mt5.get("enabled") is True and vnc.get("enabled") is True,
            # The demo timer is distinct from the newer opt-in live supervisor.
            "reconciliation_support_declared": health["live_reconciliation_supported"],
            "freeze_recovery_timer": _show(
                "dkcapital-mt5-autoheal.timer" if ident == 1
                else "dkcapital-mt5-recovery-supervisor.timer"
            ),
            "frozen_terminal_recovery_verified": False if snapshot.get("mode") == "REAL"
                else None,  # live activation requires separate broker acceptance tests
        }
    return {
        "checked_at": now.isoformat(), "accounts": accounts,
        "warning": (
            "Equal crash/boot configuration is NOT proof of matching "
            "frozen-terminal autoheal. REAL recovery additionally requires "
            "broker-history/order reconciliation proof; do not bypass."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    parser.add_argument("--data-dir", type=Path, default=None)
    options = parser.parse_args()
    data_dir = options.data_dir or discover_data_dir(options.compose_dir)
    print(json.dumps(parity_report(data_dir, datetime.now(UTC)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
