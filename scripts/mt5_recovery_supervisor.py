#!/usr/bin/env python3
"""Per-account MT5 freeze recovery with strict broker-side protection evidence.

Crash recovery is systemd Restart=always; this handles *active but frozen*
MT5 terminals. A restart never places orders, disables stops or changes EA
inputs. LIVE auto-restart is opt-in and requires an explicitly instrumented
reconciliation-capable EA. Dry run by default.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dkcapital.mt5_profile_registry import profiles_from_registry, service_unit, telemetry_file
from host_autoheal import age, discover_data_dir, read_json, save_atomic
from mt5_terminal_autoheal import pending_order_gate, gateway_healthy

STALE_SECONDS = 150
PERSIST_SECONDS = 180
SNAPSHOT_MAX_AGE = 600
RESTART_COOLDOWN = 900
MAX_RESTARTS_PER_HOUR = 2
VERIFY_AFTER_SECONDS = 150
RESTART_STATE = Path("/var/lib/dkcapital-mt5-recovery/state.json")
STATUS_NAME = "mt5-recovery-status.json"



def protected_broker_inventory(snapshot: dict[str, Any]) -> tuple[bool, str]:
    """Broker-native stop/order proof reusable pre- and post-restart."""
    for key in ("broker_positions_total", "broker_orders_total",
                "unmanaged_positions_count", "unmanaged_orders_count"):
        if type(snapshot.get(key)) is not int or snapshot[key] < 0:
            return False, f"missing broker exposure inventory: {key}"
    if snapshot.get("broker_positions_protected") is not True:
        return False, "unprotected broker position or missing SL verification"
    positions = snapshot.get("positions")
    if not isinstance(positions, list):
        return False, "missing broker positions list"
    if len(positions) + snapshot["unmanaged_positions_count"] != snapshot["broker_positions_total"]:
        return False, "broker-wide position count mismatch"
    if snapshot["unmanaged_positions_count"]:
        return False, "unmanaged positions need separate broker reconciliation"
    if snapshot["unmanaged_orders_count"]:
        return False, "unmanaged pending orders need separate broker reconciliation"
    for p in positions:
        if not isinstance(p, dict):
            return False, "invalid broker position"
        try:
            sl = float(p["sl"])
            lots = float(p["lots"])
            entry = float(p["entry"])
        except (KeyError, ValueError, TypeError):
            return False, "broker position missing native stop/volume"
        if not all(math.isfinite(v) and v > 0 for v in (sl, lots, entry)):
            return False, "broker position lacks native protective SL"
        if p.get("side") not in {"BUY", "SELL"}:
            return False, "unknown broker position side"
        if not isinstance(p.get("ticket"), int) or p["ticket"] <= 0:
            return False, "invalid broker position ticket"
    pending_ok, pending_reason = pending_order_gate(snapshot)
    if not pending_ok:
        return False, "pending order inventory unsafe: " + pending_reason
    return True, "protected broker positions and pending orders last reported; bounded restart eligible"

def broker_recovery_gate(
    profile: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    now: datetime,
    gateway_ok: bool,
    service_active: bool,
) -> tuple[bool, str]:
    """Verify last *authenticated* broker snapshot before one bounded restart.

    No stale snapshot can prove the broker's *current* orders: live restart
    additionally requires explicit EA support for broker reconciliation, which
    must be verified during the eventual account commissioning.
    """
    if not profile.get("auto_recover") or profile.get("recovery_policy") != "broker_protected":
        return False, "auto-recovery not commissioned"
    if not service_active:
        return False, "service inactive: systemd crash recovery handles this"
    if not gateway_ok:
        return False, "upstream gateway unhealthy: do not restart MT5"
    if snapshot.get("login") != profile.get("expected_login"):
        return False, "authenticated broker login mismatch"
    if snapshot.get("mode") != profile.get("expected_mode") or (
        snapshot.get("currency") != profile.get("expected_currency")
    ):
        return False, "account mode/currency mismatch"
    if snapshot.get("entry_enabled") is not profile.get("expect_entries"):
        return False, "EA trading switch does not match commissioned account intent"

    receipt_age = age(snapshot.get("received_at"), now)
    generated_age = age(snapshot.get("generated_at"), now)
    if receipt_age <= STALE_SECONDS:
        return False, "fresh MT5 telemetry; no frozen terminal"
    if not (0 <= generated_age <= SNAPSHOT_MAX_AGE):
        return False, "last broker snapshot too old or future dated"

    if profile["expected_mode"] == "REAL" and snapshot.get("restart_reconciliation_supported") is not True:
        return False, "live EA broker-history/order reconciliation not yet verified"
    return protected_broker_inventory(snapshot)


def restart_plan(prior: dict, eligible: bool, now: float) -> tuple[dict, bool, str]:
    record = prior if isinstance(prior, dict) else {}
    attempts = [
        n for n in record.get("attempts", [])
        if isinstance(n, (int, float)) and 0 <= now-n <= 3600
    ]
    first = record.get("first_seen")
    if not eligible:
        return {**record, "first_seen": None, "attempts": attempts}, False, "not eligible"
    if not isinstance(first, (float, int)) or first > now:
        first = now
    state = {**record, "first_seen": first, "attempts": attempts}
    if now-first < PERSIST_SECONDS:
        return state, False, "waiting for persistent frozen-telemetry confirmation"
    if len(attempts) >= MAX_RESTARTS_PER_HOUR:
        return state, False, "restart limit reached (2/hour)"
    if attempts and now-attempts[-1] < RESTART_COOLDOWN:
        return state, False, "restart cooldown (15 minutes)"
    return state, True, "eligible for one account-scoped restart"


def service_state(service: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["systemctl", "show", service, "-p", "LoadState",
             "-p", "ActiveState", "-p", "ExecMainStatus", "--no-pager"],
            check=False, text=True, capture_output=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "unit_state_unavailable"
    props = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    if props.get("ExecMainStatus") == "78":
        return False, "unapproved_terminal_binary_or_LiveUpdate_exit78"
    active = proc.returncode == 0 and props.get("LoadState") == "loaded" and props.get("ActiveState") == "active"
    return active, "active" if active else "service_down_systemd_recovery_expected"


def _recovered(snapshot: dict, profile: dict, now: datetime, restarted_at: str | None) -> bool:
    if not restarted_at:
        return False
    try:
        before = datetime.fromisoformat(restarted_at.replace("Z", "+00:00"))
        received = datetime.fromisoformat(str(snapshot["received_at"]).replace("Z", "+00:00"))
    except (ValueError, TypeError, KeyError):
        return False
    if before.tzinfo is None or received.tzinfo is None:
        return False
    # Successful restart requires new authenticated matching-login broker data,
    # a working feed, plus explicit reconciliation support for live profiles.
    return (
        received > before and age(snapshot.get("received_at"), now) < STALE_SECONDS
        and snapshot.get("login") == profile.get("expected_login")
        and snapshot.get("mode") == profile.get("expected_mode")
        and snapshot.get("currency") == profile.get("expected_currency")
        and snapshot.get("feed_http") == 200
        and protected_broker_inventory(snapshot)[0]
        and snapshot.get("entry_enabled") is profile.get("expect_entries")
        and (profile.get("expected_mode") != "REAL" or
             snapshot.get("restart_reconciliation_supported") is True)
    )


def cycle(
    profiles: list[dict],
    root: Path,
    previous: dict,
    now: datetime,
    *,
    apply: bool,
    gateway_ok: bool,
) -> tuple[dict, list[dict], dict]:
    results: dict = {"updated_at": now.isoformat(), "accounts": {}}
    state = dict(previous)
    accounts = state.setdefault("accounts", {})
    events: list[dict] = []
    for profile in profiles:
        key = str(profile["id"])
        service = service_unit(profile["id"])
        before = accounts.get(key) if isinstance(accounts.get(key), dict) else {}
        record = dict(before)
        snapshot = read_json(telemetry_file(root, profile["id"]))
        active, unit_reason = service_state(service)
        if not profile.get("auto_recover"):
            status = "DISABLED"
            reason = "Not commissioned for auto-recovery"
        elif not active:
            status = "SERVICE_DOWN"
            reason = unit_reason
        elif _recovered(snapshot, profile, now, record.get("restart_at")):
            status = "RECOVERED"
            reason = "fresh broker telemetry, feed and native-stop evidence after restart"
            if record.get("result") != "RECOVERED":
                events.append({"account": profile["id"], "result": "RECOVERED", "at": now.isoformat()})
            record["restart_at"] = None
        else:
            eligible, reason = broker_recovery_gate(
                profile, snapshot, now=now, gateway_ok=gateway_ok,
                service_active=active,
            )
            record, should, planning = restart_plan(record, eligible, now.timestamp())
            if should:
                if not apply:
                    status = "WOULD_RESTART"
                    reason = "dry run: " + planning
                else:
                    # Record attempt before performing any restart, so a crash
                    # in this script cannot lead to rapid duplicate retries.
                    record["attempts"] = list(record["attempts"]) + [now.timestamp()]
                    record["restart_at"] = now.isoformat()
                    record["first_seen"] = None
                    status = "RESTART_REQUESTED"
                    try:
                        proc = subprocess.run(
                            ["systemctl", "restart", service], check=False,
                            capture_output=True, text=True, timeout=90,
                        )
                        if proc.returncode != 0:
                            status = "RESTART_FAILED"
                            reason = "systemctl returned non-zero"
                        else:
                            reason = "terminal restart requested; waiting for fresh broker reconciliation"
                    except (OSError, subprocess.TimeoutExpired):
                        status = "RESTART_FAILED"
                        reason = "systemctl restart timed out or was unavailable"
                    events.append({"account": profile["id"], "result": status, "at": now.isoformat()})
            else:
                status = ("WAITING_FOR_RECONNECT" if record.get("restart_at")
                          and (now-datetime.fromisoformat(record["restart_at"])).total_seconds() >= VERIFY_AFTER_SECONDS
                          and not _recovered(snapshot, profile, now, record.get("restart_at"))
                          else "NO_RESTART")
                reason = reason + "; " + planning
                if status == "WAITING_FOR_RECONNECT" and record.get("result") != "WAITING_FOR_RECONNECT":
                    events.append({"account": profile["id"], "result": status, "at": now.isoformat()})
        record["result"] = status
        record["reason"] = reason
        accounts[key] = record
        results["accounts"][key] = {
            "result": status,
            "reason": reason,
            "last_restart_at": record.get("restart_at"),
            "auto_recover": bool(profile.get("auto_recover")),
        }
    durable = list(state.get("events") or [])
    for ev in events:
        ev["id"] = f"{ev['account']}:{ev['at']}:{ev['result']}"
        durable.append(ev)
    # Keep enough for a temporary Discord outage while bounding state size.
    state["events"] = durable[-100:]
    results["events"] = list(state["events"])
    return results, events, state


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--state-file", type=Path, default=RESTART_STATE)
    parser.add_argument("--gateway", default="http://127.0.0.1:8765/health")
    args = parser.parse_args(argv)
    try:
        root = args.data_dir or discover_data_dir(args.compose_dir)
        profiles, valid = profiles_from_registry(root)
        if not valid:
            print("BLOCKED: invalid profile registry")
            return 2
        if not args.apply:
            status, events, _ = cycle(
                profiles, root, read_json(args.state_file), datetime.now(UTC),
                apply=False, gateway_ok=gateway_healthy(args.gateway),
            )
            print(json.dumps({"mode": "DRY_RUN", **status}, indent=2))
            return 0
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
        with args.state_file.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            prior = read_json(args.state_file)
            status, events, next_state = cycle(
                profiles, root, prior, datetime.now(UTC),
                apply=True, gateway_ok=gateway_healthy(args.gateway),
            )
            # Persist first_seen and attempts as well as status, per profile.
            save_atomic(args.state_file, next_state)
            # Host-side status can be observed independently of Discord bot.
            save_atomic(root / STATUS_NAME, status)
            print(json.dumps({"mode": "APPLY", **status}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"BLOCKED: recovery supervisor error: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
