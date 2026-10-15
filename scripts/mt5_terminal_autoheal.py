#!/usr/bin/env python3
"""Opt-in recovery for a hung MT5 Account 1 terminal; DEMO ONLY.

This is distinct from the Docker autoheal. It NEVER trades, closes positions,
touches account 2, or enables the EA. Uncertain exposure fails closed.
Native broker SL remains a requirement for every reported open position.

Dry-run by default. --apply invokes only:
    systemctl restart dkcapital-mt5-1.service

The last trusted broker snapshot can become stale during a hang. A restart
cannot independently prove current broker state; use a broker-side monitor for
production/live accounts. This supervisor intentionally refuses live accounts.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from host_autoheal import age, discover_data_dir, read_json, save_atomic

TARGET_SERVICE = "dkcapital-mt5-1.service"
STALE_SECONDS = 150
PERSIST_SECONDS = 180
MAX_SNAPSHOT_AGE = 600
COOLDOWN_SECONDS = 900
MAX_RESTARTS_PER_HOUR = 2


def configured_login(compose_dir: Path) -> int:
    """Read only MT5_ACCOUNT1_LOGIN from env, never print env or credentials."""
    direct = os.getenv("MT5_ACCOUNT1_LOGIN", "").strip()
    if not direct:
        try:
            source = (compose_dir / ".env").read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError("missing account login configuration") from exc
        matches = re.findall(
            r'(?m)^\s*MT5_ACCOUNT1_LOGIN\s*=\s*["\x27]?(\d+)["\x27]?\s*(?:#.*)?$', source
        )
        if len(matches) != 1:
            raise ValueError("missing or ambiguous MT5_ACCOUNT1_LOGIN")
        direct = matches[0]
    if not direct.isdigit() or int(direct) <= 0:
        raise ValueError("invalid MT5_ACCOUNT1_LOGIN")
    return int(direct)


def gateway_healthy(url: str) -> bool:
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            return response.status == 200 and json.loads(
                response.read(2048)
            ).get("status") == "ok"
    except (OSError, ValueError, HTTPError, URLError, TimeoutError):
        return False


def recent_unreconciled_signal(data_dir: Path, snapshot_utc: datetime) -> bool:
    """Avoid restarting with a possible order sent after the last telemetry."""
    state = read_json(data_dir / "signal-state.json")
    signals = state.get("signals")
    if not isinstance(signals, list):
        return True
    for s in signals:
        if not isinstance(s, dict) or str(s.get("symbol", "")).upper() != "XAUUSD":
            continue
        candidates = [s.get("opened_at")]
        for action in s.get("history", []):
            if isinstance(action, dict):
                candidates.extend(
                    action.get(k) for k in ("timestamp", "at", "created_at")
                )
        for stamp in candidates:
            if stamp is None:
                continue
            try:
                when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return True
            if when.tzinfo is None:
                return True
            # The EA may submit after telemetry, including layered entries.
            if when.astimezone(UTC) >= snapshot_utc:
                return True
    return False


def pending_order_gate(snapshot: dict) -> tuple[bool, str]:
    """Allow only complete, recognised, broker-stopped pending XAUUSD entries.

    Older telemetry without per-order details remains safe when there are zero
    pending orders. With pending exposure it is never sufficient.
    """
    total = snapshot["broker_orders_total"]
    pending = snapshot.get("orders")
    if total == 0:
        if pending is not None and (not isinstance(pending, list) or pending):
            return False, "pending order list disagrees with zero broker orders"
        if snapshot.get("unmanaged_orders_count", 0) != 0:
            return False, "unmanaged pending order count disagrees with broker"
        return True, "no pending orders"
    unmanaged = snapshot.get("unmanaged_orders_count")
    if type(unmanaged) is not int or unmanaged != 0:
        return False, "unknown or unmatched pending orders require manual review"
    if not isinstance(pending, list) or len(pending) != total:
        return False, "incomplete broker pending order details"
    pos_magics = {p.get("magic") for p in snapshot["positions"]
                  if isinstance(p, dict)}
    seen_tickets, seen_magics = set(), set()
    for order in pending:
        if not isinstance(order, dict):
            return False, "malformed broker pending order"
        ticket, magic = order.get("ticket"), order.get("magic")
        if type(ticket) is not int or ticket <= 0 or ticket in seen_tickets:
            return False, "missing or duplicate broker pending ticket"
        if (type(magic) is not int or
                not 5100000001 <= magic <= 48049672953 or
                magic % 10 not in (1, 2, 3, 4) or
                magic in seen_magics or magic in pos_magics):
            return False, "unknown or conflicting broker pending order magic"
        if order.get("symbol") != "XAUUSD":
            return False, "unexpected pending order symbol"
        kind = order.get("type")
        if kind not in ("BUY_LIMIT", "BUY_STOP", "SELL_LIMIT", "SELL_STOP"):
            return False, "unsupported pending order type"
        try:
            price, sl, lots = (float(order[k]) for k in ("price", "sl", "lots"))
            tp = float(order.get("tp", 0))
        except (KeyError, TypeError, ValueError, OverflowError):
            return False, "incomplete pending order risk details"
        if (not all(math.isfinite(v) for v in (price, sl, lots, tp))
                or min(price, sl, lots) <= 0 or tp < 0):
            return False, "pending order missing native protective stop or volume"
        if (kind.startswith("BUY") and sl >= price or
                kind.startswith("SELL") and sl <= price):
            return False, "pending broker stop is on the wrong side of entry"
        seen_tickets.add(ticket)
        seen_magics.add(magic)
    return True, "reconciled owned pending entries with broker-native stops"


def recovery_gate(snapshot: dict, *, expected_login: int, now: datetime,
                  signals_ambiguous: bool, gateway_ok: bool,
                  service_active: bool) -> tuple[bool, str]:
    """Fail closed on incomplete, old or unprotected broker evidence."""
    if not service_active:
        return False, "systemd service inactive; defer to its Restart=always policy"
    if not gateway_ok:
        return False, "gateway unhealthy; do not restart MT5 for upstream outage"
    if snapshot.get("mode") != "DEMO" or snapshot.get("currency") != "GBP":
        return False, "requires GBP DEMO account"
    if snapshot.get("login") != expected_login:
        return False, "account login not independently matched"
    if snapshot.get("entry_enabled") is not True:
        return False, "new entries intentionally disabled; no unattended MT5 restart"
    if age(snapshot.get("received_at"), now) <= STALE_SECONDS:
        return False, "telemetry currently fresh"
    snapshot_age = age(snapshot.get("generated_at"), now)
    if snapshot_age > MAX_SNAPSHOT_AGE:
        return False, "broker snapshot too old for a guarded restart"
    if snapshot_age < 0:
        return False, "broker snapshot in future"
    for name in ("broker_positions_total", "broker_orders_total", "unmanaged_positions_count"):
        if type(snapshot.get(name)) is not int or snapshot[name] < 0:
            return False, f"missing account-wide exposure field {name}"
    if type(snapshot.get("broker_positions_protected")) is not bool:
        return False, "missing native stop protection proof"
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        return False, "missing owned broker position list"
    count = snapshot["broker_positions_total"]
    unmanaged = snapshot["unmanaged_positions_count"]
    if count != unmanaged + len(positions):
        return False, "account-wide and owned position counts disagree"
    pending_ok, pending_reason = pending_order_gate(snapshot)
    if not pending_ok:
        return False, pending_reason
    if unmanaged != 0:
        return False, "unmanaged broker positions require manual reconciliation"
    if not snapshot["broker_positions_protected"]:
        return False, "one or more positions lack native protective stops"
    for item in positions:
        if not isinstance(item, dict) or float(item.get("sl") or 0) <= 0:
            return False, "one or more owned positions have no native stop"
    if signals_ambiguous:
        return False, "recent signal or management event could have changed broker exposure"
    return True, ("guarded DEMO restart eligible: complete last-known exposure; "
                  + pending_reason + "; all positions stopped")


def plan(state: dict, eligible: bool, now_epoch: float) -> tuple[dict, bool, str]:
    record = state if isinstance(state, dict) else {}
    if not eligible:
        return {"first_seen": None, "attempts": record.get("attempts", [])}, False, "ineligible"
    first = record.get("first_seen")
    if not isinstance(first, (int, float)) or first > now_epoch:
        first = now_epoch
    attempts = [
        t for t in record.get("attempts", [])
        if isinstance(t, (int, float)) and 0 <= now_epoch - t <= 3600
    ]
    updated = {"first_seen": first, "attempts": attempts}
    if now_epoch - first < PERSIST_SECONDS:
        return updated, False, "waiting for persistent confirmed hang"
    if len(attempts) >= MAX_RESTARTS_PER_HOUR:
        return updated, False, "hourly restart budget exhausted"
    if attempts and now_epoch - attempts[-1] < COOLDOWN_SECONDS:
        return updated, False, "restart cooldown"
    return updated, True, "safe restart budget available"


def service_running() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", TARGET_SERVICE],
            timeout=5, capture_output=True, check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--state-file", type=Path,
                        default=Path("/var/lib/dkcapital-mt5-autoheal/state.json"))
    parser.add_argument("--gateway", default="http://127.0.0.1:8765/health")
    args = parser.parse_args(argv)
    try:
        expected = configured_login(args.compose_dir)
        data_dir = args.data_dir or discover_data_dir(args.compose_dir)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: insufficient configuration: {type(exc).__name__}: {exc}")
        return 2
    now = datetime.now(UTC)
    snapshot = read_json(data_dir / "mt5-account1.json")
    generated_at = snapshot.get("generated_at")
    try:
        snapshot_time = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if snapshot_time.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError):
        snapshot_time = datetime.min.replace(tzinfo=UTC)
    ambiguous = recent_unreconciled_signal(data_dir, snapshot_time)
    eligible, reason = recovery_gate(
        snapshot, expected_login=expected, now=now,
        signals_ambiguous=ambiguous, gateway_ok=gateway_healthy(args.gateway),
        service_active=service_running(),
    )
    if not args.apply:
        updated, should_restart, planning = plan(
            read_json(args.state_file), eligible, now.timestamp(),
        )
        print(json.dumps({
            "mode": "DRY_RUN", "target": TARGET_SERVICE, "eligible": eligible,
            "reason": reason, "would_restart": should_restart, "planning": planning,
            "last_snapshot_age_seconds": round(age(generated_at, now)),
        }, indent=2))
        return 0

    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    with args.state_file.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state, should_restart, planning = plan(
            read_json(args.state_file), eligible, now.timestamp(),
        )
        if should_restart:
            # Commit restart intent before invoking systemd, including failed
            # attempts, so timeouts/reboots cannot trigger rapid loops.
            state["attempts"].append(now.timestamp())
            save_atomic(args.state_file, state)
            try:
                subprocess.run(
                    ["systemctl", "restart", TARGET_SERVICE],
                    check=True, timeout=90, capture_output=True, text=True,
                )
                print("RESTART_REQUESTED: verify new MT5 telemetry and native broker stops")
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                print(f"RESTART_FAILED: {type(exc).__name__}", file=sys.stderr)
                return 1
        else:
            save_atomic(args.state_file, state)
            print(f"NO_RESTART: {reason}; {planning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
