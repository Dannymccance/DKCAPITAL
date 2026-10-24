#!/usr/bin/env python3
"""Read-only host-side health snapshot for each commissioned MT5 instance.

No restarts, trading commands, passwords, or writes to MT5/Wine directories.
The Docker watchdog consumes this independently produced systemd state.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dkcapital.mt5_profile_registry import profiles_from_registry, service_unit
from host_autoheal import discover_data_dir, read_json, save_atomic


def inspect_unit(ident: int) -> dict:
    service = service_unit(ident)
    try:
        proc = subprocess.run([
            "systemctl", "show", service, "--no-pager",
            "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
            "-p", "NRestarts", "-p", "MainPID",
        ], text=True, capture_output=True, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"active": False, "load_state": "unknown", "sub_state": "unknown",
                "restart_count": None, "main_pid": None}
    values = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    try:
        restarts = int(values.get("NRestarts", ""))
        pid = int(values.get("MainPID", ""))
    except ValueError:
        restarts = pid = None
    return {
        "active": proc.returncode == 0 and values.get("ActiveState") == "active"
                  and values.get("LoadState") == "loaded",
        "load_state": values.get("LoadState", "unknown"),
        "sub_state": values.get("SubState", "unknown"),
        "restart_count": restarts, "main_pid": pid,
    }


def build_snapshot(profiles: list[dict], prior: dict, now: datetime) -> dict:
    previous = prior.get("accounts") if isinstance(prior.get("accounts"), dict) else {}
    accounts = {}
    for p in profiles:
        ident = str(p["id"])
        state = inspect_unit(p["id"])
        old = previous.get(ident)
        last_event = None
        if isinstance(old, dict):
            last_event = old.get("last_restart_at")
            was = old.get("restart_count")
            is_now = state.get("restart_count")
            old_pid = old.get("main_pid")
            new_pid = state.get("main_pid")
            if (
                type(was) is int and type(is_now) is int and is_now > was
                or type(old_pid) is int and old_pid > 0
                and type(new_pid) is int and new_pid > 0 and new_pid != old_pid
            ):
                last_event = now.isoformat()
        state["last_restart_at"] = last_event
        accounts[ident] = state
    return {"checked_at": now.isoformat(), "accounts": accounts}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        data_dir = args.data_dir or discover_data_dir(args.compose_dir)
        profiles, valid = profiles_from_registry(data_dir)
        if not valid:
            print("BLOCKED: MT5 monitor registry is invalid; retaining previous host snapshot")
            return 2
        target = data_dir / "mt5-profile-host-status.json"
        before = read_json(target)
        state = build_snapshot(profiles, before, datetime.now(UTC))
        save_atomic(target, state)
        print(json.dumps({
            "status": "ok",
            "profile_count": len(profiles),
            "services": {k: v["active"] for k, v in state["accounts"].items()},
        }))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"FAILED: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
