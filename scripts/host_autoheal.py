#!/usr/bin/env python3
"""Bounded host-side recovery for DK Capital application services.

Never restarts MT5 terminals, enables trading, modifies stops, or places orders.
Read-only by default. --apply is required for any Compose service restart.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

RESTARTABLE = {
    "GATEWAY_HEALTH_UNAVAILABLE": "mt5-gateway",
    "SIGNAL_PROCESSOR_HEARTBEAT_STALE": "signal-processor",
    "SIGNAL_PROCESSOR_EVENT_BACKLOG": "signal-processor",
    "TELEGRAM_CATCHUP_HEARTBEAT_STALE": "telegram-listener",
    "DISCORD_BOT_HEARTBEAT_STALE": "discord-bot",
}
PERSIST_SECONDS = 180
COOLDOWN_SECONDS = 900
MAX_RESTARTS_PER_HOUR = 2


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def age(value, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return float("inf")
        return max(0.0, (now - parsed.astimezone(UTC)).total_seconds())
    except (ValueError, TypeError):
        return float("inf")


def discover_data_dir(compose_dir: Path) -> Path:
    """Find the actual data mount from the processor container.

    Compose uses a named Docker volume by default, not /opt/dkcapital/data.
    Resolve it rather than accidentally restarting workers on false stale
    heartbeat reports from an unrelated or nonexistent host directory.
    """
    ps = subprocess.run(
        ["docker", "compose", "ps", "-aq", "signal-processor"],
        cwd=compose_dir, check=True, capture_output=True, text=True, timeout=15,
    )
    ids = [s for s in ps.stdout.splitlines() if s.strip()]
    if len(ids) != 1:
        raise RuntimeError("Cannot uniquely identify signal-processor container")
    details = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", ids[0].strip()],
        check=True, capture_output=True, text=True, timeout=15,
    )
    mounts = json.loads(details.stdout)
    paths = [
        Path(m["Source"]) for m in mounts
        if m.get("Destination") == "/app/data" and m.get("Source")
    ]
    if len(paths) != 1 or not paths[0].is_dir():
        raise RuntimeError("Cannot uniquely identify mounted /app/data directory")
    return paths[0]


def probe(root: Path, gateway: str, now: datetime) -> set[str]:
    problems = set()
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(gateway, timeout=5) as response:
            payload = response.read(2048)
            if response.status != 200 or json.loads(payload).get("status") != "ok":
                problems.add("GATEWAY_HEALTH_UNAVAILABLE")
    except (OSError, ValueError, TimeoutError):
        problems.add("GATEWAY_HEALTH_UNAVAILABLE")

    for filename, field, threshold, issue in (
        ("signal-processor-heartbeat.json", "checked_at", 200,
         "SIGNAL_PROCESSOR_HEARTBEAT_STALE"),
        ("telegram-recovery-status.json", "checked_at", 200,
         "TELEGRAM_CATCHUP_HEARTBEAT_STALE"),
        ("discord-heartbeat.json", "checked_at", 200,
         "DISCORD_BOT_HEARTBEAT_STALE"),
        ("mt5-account1.json", "received_at", 150,
         "MT5_ACCOUNT1_TELEMETRY_STALE"),
    ):
        if age(read_json(root / filename).get(field), now) > threshold:
            problems.add(issue)

    telemetry = read_json(root / "mt5-account1.json")
    if "MT5_ACCOUNT1_TELEMETRY_STALE" not in problems:
        if telemetry.get("feed_http") != 200:
            problems.add("MT5_ACCOUNT1_FEED_NOT_HTTP200")
        if telemetry.get("entry_enabled") is not True:
            problems.add("MT5_ACCOUNT1_NOT_ARMED")

    catchup = read_json(root / "telegram-recovery-status.json")
    if ("TELEGRAM_CATCHUP_HEARTBEAT_STALE" not in problems
            and catchup.get("healthy") is not True):
        problems.add("TELEGRAM_CATCHUP_INCOMPLETE")

    processor = read_json(root / "signal-processor-heartbeat.json")
    if "SIGNAL_PROCESSOR_HEARTBEAT_STALE" not in problems:
        events = root / "telegram-events.jsonl"
        try:
            offset = int(processor.get("event_log_offset", -1))
            stat = events.stat()
            if (offset >= 0 and stat.st_size > offset
                    and now.timestamp() - stat.st_mtime > 60):
                problems.add("SIGNAL_PROCESSOR_EVENT_BACKLOG")
        except (OSError, ValueError, TypeError):
            pass
    return problems


def plan(state: dict, issues: set[str], now_epoch: float):
    """Decide at most one bounded restart; no side effects."""
    incidents = state.get("incidents")
    incidents = dict(incidents) if isinstance(incidents, dict) else {}
    for issue in list(incidents):
        if issue not in issues:
            incidents.pop(issue)

    selected = None
    notes = []
    for issue in sorted(issues):
        if issue not in RESTARTABLE:
            notes.append(f"ALERT_ONLY {issue}: no automatic restart")
            continue

        record = incidents.get(issue, {})
        if not isinstance(record, dict):
            record = {}
        first = float(record.get("first_seen", now_epoch))
        if first > now_epoch:
            first = now_epoch
        attempts = [
            float(t) for t in record.get("attempts", [])
            if isinstance(t, (int, float)) and 0 <= now_epoch - t <= 3600
        ]
        incidents[issue] = {"first_seen": first, "attempts": attempts}
        if now_epoch - first < PERSIST_SECONDS:
            notes.append(f"WAIT {issue}: persistent {int(now_epoch-first)}/{PERSIST_SECONDS}s")
        elif len(attempts) >= MAX_RESTARTS_PER_HOUR:
            notes.append(f"ALERT_ONLY {issue}: hourly restart budget exceeded")
        elif attempts and now_epoch - attempts[-1] < COOLDOWN_SECONDS:
            notes.append(f"WAIT {issue}: restart cooldown")
        elif selected is None:
            selected = RESTARTABLE[issue]
            notes.append(f"ELIGIBLE {issue}: {selected}")
    return {"incidents": incidents}, selected, notes


def save_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Optional override for detected Docker /app/data mount")
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    parser.add_argument("--state-file", type=Path,
                        default=Path("/var/lib/dkcapital-autoheal/state.json"))
    parser.add_argument("--gateway", default="http://127.0.0.1:8765/health")
    parser.add_argument("--apply", action="store_true",
                        help="Allow bounded restarts of selected Compose services")
    args = parser.parse_args(argv)

    if not args.compose_dir.is_dir() or not any(
        (args.compose_dir / name).exists()
        for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yaml")
    ):
        print("ERROR: missing Compose directory/manifest", file=sys.stderr)
        return 2

    try:
        data_dir = args.data_dir or discover_data_dir(args.compose_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: cannot resolve app data mount: {exc}", file=sys.stderr)
        return 2
    if not data_dir.is_dir():
        print("ERROR: data directory does not exist", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    issues = probe(data_dir, args.gateway, now)
    if not args.apply:
        _, candidate, notes = plan(read_json(args.state_file), issues, now.timestamp())
        print(json.dumps({
            "mode": "DRY_RUN", "data_dir": str(data_dir),
            "issues": sorted(issues), "candidate": candidate, "notes": notes,
        }, indent=2))
        return 0

    args.state_file.parent.mkdir(parents=True, exist_ok=True)
    with args.state_file.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state, service, notes = plan(read_json(args.state_file), issues, now.timestamp())
        print(json.dumps({
            "mode": "ENABLED", "issues": sorted(issues),
            "candidate": service, "notes": notes,
        }))
        if service:
            try:
                subprocess.run(
                    ["docker", "compose", "restart", service],
                    cwd=args.compose_dir, check=True, timeout=90,
                    capture_output=True, text=True,
                )
                print(f"RESTARTED {service}; verify independent watchdog recovery")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                print(f"RESTART_FAILED {service}: {type(exc).__name__}", file=sys.stderr)
            finally:
                # Failed starts also consume cooldown and restart budget.
                for issue, target in RESTARTABLE.items():
                    if issue in state["incidents"] and target == service:
                        state["incidents"][issue]["attempts"].append(now.timestamp())
        save_atomic(args.state_file, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
