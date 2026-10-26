#!/usr/bin/env python3
"""Commission all three profiles for existing READ-ONLY host audit.

This deliberately does NOT enable the live frozen-terminal recovery supervisor,
touch a running MT5, change the EA or submit broker orders. REAL profiles are
monitor-only until verified broker-history reconciliation is implemented.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from dkcapital.mt5_profile_registry import PROFILE_REGISTRY_NAME, profiles_from_registry
from host_autoheal import discover_data_dir
from mt5_fleet_acceptance_readonly import assess, private_values

PROFILES = (
    {"id": 1, "enabled": True, "label": "Account 1 DEMO",
     "expected_login": 5057220041, "expected_mode": "DEMO",
     "expected_currency": "GBP", "expect_entries": True,
     "auto_recover": False, "recovery_policy": "disabled"},
    {"id": 2, "enabled": True, "label": "Kyle Account 2 REAL",
     "expected_login": 24038172, "expected_mode": "REAL",
     "expected_currency": "GBP", "expect_entries": True,
     "auto_recover": False, "recovery_policy": "disabled"},
    {"id": 3, "enabled": True, "label": "Dan Account 3 REAL",
     "expected_login": 36193223, "expected_mode": "REAL",
     "expected_currency": "USD", "expect_entries": True,
     "auto_recover": False, "recovery_policy": "disabled"},
)


def propose(existing: dict | None) -> dict:
    """Only add missing profiles; never silently replace existing guard policies."""
    if existing is None:
        rows = []
    elif not isinstance(existing, dict) or not isinstance(existing.get("accounts"), list):
        raise ValueError("Existing registry format invalid")
    else:
        rows = list(existing["accounts"])
    seen = set()
    by_id = {}
    for entry in rows:
        if not isinstance(entry, dict) or type(entry.get("id")) is not int:
            raise ValueError("Existing registry contains invalid entry")
        ident = entry["id"]
        if ident in seen:
            raise ValueError("Duplicate profile in existing registry")
        seen.add(ident)
        by_id[ident] = entry
    for required in PROFILES:
        ident = required["id"]
        if ident in by_id:
            actual = by_id[ident]
            if actual.get("enabled") is not True:
                raise ValueError(f"Existing Account{ident} disabled: operator review required")
            for key in ("expected_login", "expected_mode", "expected_currency",
                        "expect_entries"):
                if actual.get(key) != required[key]:
                    raise ValueError(f"Account{ident} existing {key} conflicts with verified identity")
            if ident in (2, 3) and (
                actual.get("auto_recover") is not False
                or actual.get("recovery_policy") != "disabled"
            ):
                raise ValueError(f"Account{ident} REAL auto-recovery must not be silently modified")
        else:
            rows.append(dict(required))
    result = {"accounts": rows}
    if existing:
        result.update({k: v for k, v in existing.items() if k != "accounts"})
    return result


def verify_registry_contents(payload: dict) -> list[dict]:
    # Validate with the exact registry parser the running audit uses.
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / PROFILE_REGISTRY_NAME
        path.write_text(json.dumps(payload))
        parsed, valid = profiles_from_registry(Path(temp))
    if not valid:
        raise ValueError("Proposed profiles rejected by runtime parser")
    if not {1, 2, 3}.issubset({p["id"] for p in parsed}):
        raise ValueError("Profile registration incomplete")
    return parsed


def commission(*, install: bool, compose_dir: Path) -> dict:
    data = discover_data_dir(compose_dir)
    target = data / PROFILE_REGISTRY_NAME
    if target.is_symlink():
        raise ValueError("Refusing symlinked monitoring registry")
    # Register only when authenticated broker identities and feed protections are good.
    live_report = assess(private_values(), expect_all_armed=True)
    if live_report["overall"] != "PASS":
        raise ValueError("All-three authenticated broker acceptance must PASS")
    existing = json.loads(target.read_text()) if target.exists() else None
    candidate = propose(existing)
    registered = verify_registry_contents(candidate)
    p = subprocess.run(["systemctl", "is-active", "dkcapital-mt5-profile-audit.timer"],
                       check=False, capture_output=True, text=True, timeout=8)
    if p.returncode != 0 or p.stdout.strip() != "active":
        raise ValueError("Existing independent host audit timer is not active")
    if install:
        active = subprocess.run(
            ["git", "-C", str(compose_dir), "branch", "--show-current"],
            check=True, capture_output=True, text=True, timeout=8).stdout.strip()
        if active != "main":
            raise ValueError("Only reviewed main may commission profiles")
        if existing is not None:
            backup_dir = Path("/var/backups/dkcapital-recovery")
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / ("mt5-monitor-registry-" +
                                  datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f") + ".json")
            with backup.open("x") as handle:
                json.dump(existing, handle, indent=2)
            backup.chmod(0o600)
        fd, temp = tempfile.mkstemp(prefix=".mt5-monitor-", dir=data)
        try:
            with os.fdopen(fd, "w") as writer:
                json.dump(candidate, writer, indent=2)
                writer.write("\n")
            os.chmod(temp, 0o644)
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return {
        "mode": "MONITOR_REGISTRY_INSTALLED" if install else "AUDIT_ONLY",
        "profile_ids": [p["id"] for p in registered],
        "account2_auto_recover": False,
        "account3_auto_recover": False,
        "host_audit_timer": "active",
        "mt5_services_restarted": False,
        "broker_orders_changed": False,
        "live_frozen_terminal_restart": "NOT ENABLED: requires verified reconciliation",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="Write independent host-monitoring registry; no service changes")
    parser.add_argument("--compose-dir", type=Path, default=Path("/opt/dkcapital"))
    args = parser.parse_args()
    try:
        result = commission(install=args.install, compose_dir=args.compose_dir)
    except (OSError, ValueError, KeyError, RuntimeError, json.JSONDecodeError,
            subprocess.SubprocessError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
