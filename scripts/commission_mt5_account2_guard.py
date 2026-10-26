#!/usr/bin/env python3
"""Audit, or opt-in stage, Kyle's MT5 hash-pinned crash/boot guard.

No systemd restart, no broker commands, no change to EA or trading flags.
The common launcher also serves Account 1, so use a controlled maintenance
window; this verifies Account1's existing pin before a shared binary change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path("/opt/dkcapital")
GOOD = "2e3c9aea103e7f4444bb917454b0a740372400c7f7a6c0e0d36926e81098b9a1"
ACCOUNT1 = Path("/var/lib/dkcapital-mt5/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe")
ACCOUNT2 = Path("/var/lib/dkcapital-mt5-2/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe")
APPROVAL1 = Path("/etc/dkcapital/mt5-account1-approved.sha256")
APPROVAL2 = Path("/etc/dkcapital/mt5-account2-approved.sha256")
LAUNCHER = Path("/usr/local/bin/dkcapital-mt5-run")
REVIEWED = ROOT / "ops/mt5/dkcapital-mt5-run"
DROPIN_DIR = Path("/etc/systemd/system/dkcapital-mt5-2.service.d")
DROPIN = DROPIN_DIR / "20-approved-binary.conf"
UNIT = "dkcapital-mt5-2.service"
OVERRIDE = "[Service]\nRestartPreventExitStatus=78\n"


def run(*args: str) -> str:
    p = subprocess.run(args, capture_output=True, text=True, check=True, timeout=12)
    return p.stdout.strip()


def digest(p: Path) -> str:
    if p.is_symlink() or not p.is_file():
        raise ValueError("missing or symlinked MT5 executable / root approval file")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def audit() -> dict:
    if os.geteuid() != 0:
        raise ValueError("Run as root on the VPS")
    if run("git", "-C", str(ROOT), "branch", "--show-current") != "main":
        raise ValueError("Reviewed main branch required")
    if ACCOUNT1.is_symlink() or ACCOUNT2.is_symlink() or LAUNCHER.is_symlink():
        raise ValueError("Unexpected executable/launcher symlink")
    if digest(ACCOUNT1) != GOOD or digest(ACCOUNT2) != GOOD:
        raise ValueError("Account1/2 MT5 executable is not the verified known-working SHA")
    if APPROVAL1.is_symlink() or APPROVAL1.read_text().strip().lower() != GOOD:
        raise ValueError("Account1 root-managed pin is invalid")
    if not REVIEWED.is_file() or not LAUNCHER.is_file():
        raise ValueError("Required common launcher absent")
    reviewed = REVIEWED.read_text()
    for expected in ('ACCOUNT2_APPROVAL_FILE', 'in ("1", "2", "3")',
                     'APPROVAL_FAILURE_EXIT = 78'):
        if expected not in reviewed:
            raise ValueError("Reviewed launcher missing Kyle fail-closed update logic")
    for n in (1, 2, 3):
        if run("systemctl", "is-active", f"dkcapital-mt5-{n}.service") != "active":
            raise ValueError(f"Account{n} MT5 service inactive")
    if run("systemctl", "show", "-P", "User", UNIT) != "dkmt5b":
        raise ValueError("Account2 systemd service has wrong Linux user")
    if run("systemctl", "show", "-P", "Restart", UNIT) != "always":
        raise ValueError("Account2 process restart not configured")
    startup = run("systemctl", "show", "-P", "ExecStart", UNIT)
    if str(LAUNCHER) not in startup or " 2" not in startup:
        raise ValueError("Account2 startup does not use the expected common launcher")
    # Fail closed if Kyle is not in the verified *flat and healthy* live state.
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        if k.startswith("MT5_ACCOUNT2_"):
            if k in env:
                raise ValueError("duplicate private Account2 configuration")
            env[k] = v.strip().strip('"').strip("'")
    expected = {"MT5_ACCOUNT2_LOGIN": "24038172", "MT5_ACCOUNT2_MODE": "REAL",
                "MT5_ACCOUNT2_CURRENCY": "GBP", "MT5_ACCOUNT2_SERVER": "VantageMarkets-Live 14"}
    if any(env.get(k) != v for k, v in expected.items()):
        raise ValueError("Kyle Account2 private broker identity mismatch")
    token = env.get("MT5_ACCOUNT2_TOKEN", "")
    if len(token) != 64:
        raise ValueError("Kyle Account2 token not configured")
    with urlopen(Request(
        "http://127.0.0.1:8765/v5/accounts/2/snapshot",
        headers={"Authorization": "Bearer " + token},
    ), timeout=8) as response:
        snapshot = json.load(response)
    stamp = datetime.fromisoformat(str(snapshot.get("received_at")).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Unverified telemetry time zone")
    age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
    if not (
        0 <= age <= 150 and snapshot.get("login") == 24038172
        and snapshot.get("mode") == "REAL"
        and snapshot.get("currency") == "GBP"
        and snapshot.get("server") == "VantageMarkets-Live 14"
        and snapshot.get("feed_http") == 200
        and snapshot.get("broker_positions_protected") is True
        and snapshot.get("broker_positions_total") == 0
        and snapshot.get("broker_orders_total") == 0
        and snapshot.get("unmanaged_positions_count") == 0
        and snapshot.get("unmanaged_orders_count") == 0
    ):
        raise ValueError("Kyle is not authenticated, current, flat and broker-protected")
    if APPROVAL2.exists() and (
        APPROVAL2.is_symlink() or APPROVAL2.read_text().strip().lower() != GOOD
    ):
        raise ValueError("An existing Kyle pin differs from the verified hash")
    if DROPIN.exists() and (
        DROPIN.is_symlink() or DROPIN.read_text() != OVERRIDE
    ):
        raise ValueError("Existing Account2 service override conflicts")
    return {
        "profile": "Kyle Account2 REAL GBP",
        "verified_mt5_sha": GOOD,
        "fresh_broker_snapshot": True,
        "positions": 0,
        "orders": 0,
        "account1_pin_valid": True,
        "account2_pin_installed": APPROVAL2.exists(),
        "account2_override_installed": DROPIN.exists(),
        "current_restart_prevent_exit_status":
            run("systemctl", "show", "-P", "RestartPreventExitStatus", UNIT),
        "warning": "Auditing does not mean frozen-terminal recovery is commissioned",
    }


def write_root_file(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def install():
    status = audit()
    backup = Path("/var/backups/dkcapital-recovery") / (
        "kyle-guard-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f"))
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    shutil.copy2(LAUNCHER, backup / "common-launcher.before")
    run("python3", "-m", "py_compile", str(REVIEWED))
    write_root_file(APPROVAL2, (GOOD + "\n").encode(), 0o644)
    write_root_file(DROPIN, OVERRIDE.encode(), 0o644)
    write_root_file(LAUNCHER, REVIEWED.read_bytes(), 0o755)
    run("systemctl", "daemon-reload")
    if "78" not in run("systemctl", "show", "-P", "RestartPreventExitStatus", UNIT):
        raise RuntimeError("Account2 systemd exit-78 override not applied; review saved backup")
    print(json.dumps({"result": "STAGED", "backup": str(backup),
                      "account2_live_mt5_restarted": False,
                      "account1_or_account3_restarted": False,
                      "frozen_terminal_recovery": "NOT COMMISSIONED"}, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="Install pinned launcher/drop-in; no MT5 restart")
    args = parser.parse_args()
    try:
        if args.install:
            install()
        else:
            print(json.dumps(audit(), indent=2))
            print("READ ONLY: No service was changed.")
        return 0
    except (OSError, KeyError, ValueError, RuntimeError, subprocess.SubprocessError) as err:
        print(f"BLOCKED: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
