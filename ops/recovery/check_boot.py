#!/usr/bin/env python3
"""Read-only DK Capital reboot audit; never places trades or restarts services."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import subprocess
import sys

EXPECTED_CONTAINERS = (
    "dkcapital-telegram-listener-1",
    "dkcapital-signal-processor-1",
    "dkcapital-paper-engine-1",
    "dkcapital-discord-bot-1",
    "dkcapital-mt5-gateway-1",
    "dkcapital-dkcapital-watchdog-1",
)
MT5_SERVICES = ("dkcapital-mt5-1.service", "dkcapital-mt5-2.service")
VNC_SERVICES = ("dkcapital-vnc1.service", "dkcapital-vnc2.service")
EXPECTED_LOGIN = 5057220041
MAX_TELEMETRY_AGE_SECONDS = 180


def run(*args: str, timeout: int = 12) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            args, text=True, capture_output=True, timeout=timeout, check=False
        )
        return proc.returncode == 0, proc.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, ""


def telemetry_errors(snapshot: dict, now: datetime) -> list[str]:
    errors = []
    if not isinstance(snapshot, dict) or not snapshot:
        return ["MT5_ACCOUNT1_TELEMETRY_MISSING"]
    if snapshot.get("login") != EXPECTED_LOGIN:
        errors.append("MT5_ACCOUNT1_WRONG_LOGIN")
    if snapshot.get("mode") != "DEMO" or snapshot.get("currency") != "GBP":
        errors.append("MT5_ACCOUNT1_WRONG_ACCOUNT_TYPE")
    if snapshot.get("signal_risk_cap_pct") != 4.0 or snapshot.get("daily_loss_guard_mode") != "ALERT_ONLY":
        errors.append("MT5_ACCOUNT1_V205_NOT_VERIFIED")
    if snapshot.get("feed_http") != 200:
        errors.append("MT5_ACCOUNT1_FEED_UNAVAILABLE")
    if snapshot.get("broker_positions_protected") is False:
        errors.append("MT5_ACCOUNT1_BROKER_STOP_PROTECTION_MISMATCH")
    try:
        received = datetime.fromisoformat(str(snapshot["received_at"]).replace("Z", "+00:00"))
        if received.tzinfo is None:
            raise ValueError("naive time")
        age = (now - received.astimezone(UTC)).total_seconds()
        if age < -15 or age > MAX_TELEMETRY_AGE_SECONDS:
            errors.append("MT5_ACCOUNT1_TELEMETRY_STALE")
    except (KeyError, ValueError, TypeError, OverflowError):
        errors.append("MT5_ACCOUNT1_TELEMETRY_STALE")
    return errors


def collect(*, vnc: bool = False) -> tuple[list[str], dict]:
    errors: list[str] = []
    details: dict = {}
    for unit in ("docker.service", *MT5_SERVICES, *(VNC_SERVICES if vnc else ())):
        ok, status = run("systemctl", "is-active", unit)
        details[unit] = status or "unknown"
        if not ok or status != "active":
            errors.append(f"UNIT_DOWN:{unit}")

    for container in EXPECTED_CONTAINERS:
        ok, status = run("docker", "inspect", "--format", "{{.State.Running}}", container)
        details[container] = status or "unknown"
        if not ok or status != "true":
            errors.append(f"CONTAINER_DOWN:{container}")

    # All values are broker telemetry metadata; never print authentication tokens.
    code = (
        "import json;"
        "from pathlib import Path;"
        "p=Path('/app/data/mt5-account1.json');"
        "d=json.loads(p.read_text());"
        "keys=('login','mode','currency','signal_risk_cap_pct','daily_loss_guard_mode',"
        "'feed_http','received_at','broker_positions_total','broker_orders_total',"
        "'broker_positions_protected','entry_enabled','layer_entries_enabled');"
        "print(json.dumps({k:d.get(k) for k in keys}))"
    )
    ok, payload = run(
        "docker", "exec", "dkcapital-mt5-gateway-1", "python", "-c", code
    )
    if not ok:
        errors.append("MT5_ACCOUNT1_TELEMETRY_UNAVAILABLE")
    else:
        try:
            snapshot = json.loads(payload)
        except (ValueError, TypeError):
            errors.append("MT5_ACCOUNT1_TELEMETRY_MALFORMED")
        else:
            errors.extend(telemetry_errors(snapshot, datetime.now(UTC)))
            details["mt5_account1"] = snapshot

    return sorted(set(errors)), details


def main() -> int:
    vnc = "--require-vnc" in sys.argv[1:]
    errors, details = collect(vnc=vnc)
    print(json.dumps({
        "status": "ok" if not errors else "degraded",
        "time": datetime.now(UTC).isoformat(),
        "problems": errors,
        "components": details,
    }, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
