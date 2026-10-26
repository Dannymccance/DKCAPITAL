#!/usr/bin/env python3
"""Audit and commission Account3 boot recovery. No restarts or orders."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

EXE = Path("/var/lib/dkcapital-mt5-3/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe")
PIN = Path("/etc/dkcapital/mt5-account3-approved.sha256")
UNITS = ("dkcapital-mt5-3.service", "dkcapital-vnc3.service")
ALL = ("dkcapital-mt5-1.service", "dkcapital-mt5-2.service") + UNITS

def call(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=12).stdout.strip()

def check():
    for unit in ALL:
        if call("systemctl", "is-active", unit) != "active":
            raise ValueError("required service inactive: " + unit)
    if call("systemctl", "show", "-P", "Restart", UNITS[0]) != "always":
        raise ValueError("Account3 crash restart is not enabled")
    if EXE.is_symlink() or PIN.is_symlink():
        raise ValueError("symlinked executable or approval")
    if hashlib.sha256(EXE.read_bytes()).hexdigest() != PIN.read_text().strip():
        raise ValueError("Account3 MT5 executable approval mismatch")
    config = {}
    for line in Path("/opt/dkcapital/.env").read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key in {"MT5_ACCOUNT3_LOGIN", "MT5_ACCOUNT3_MODE",
                   "MT5_ACCOUNT3_CURRENCY", "MT5_ACCOUNT3_SERVER", "MT5_ACCOUNT3_TOKEN"}:
            if key in config:
                raise ValueError("duplicate Account3 configuration")
            config[key] = value.strip()
    if len(config) != 5 or not all(config.values()):
        raise ValueError("missing private Account3 configuration")
    with urlopen(Request(
        "http://127.0.0.1:8765/v5/accounts/3/snapshot",
        headers={"Authorization": "Bearer " + config["MT5_ACCOUNT3_TOKEN"]}
    ), timeout=10) as response:
        broker = json.load(response)
    stamp = datetime.fromisoformat(str(broker["received_at"]).replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("broker timestamp without UTC offset")
    age = (datetime.now(UTC) - stamp.astimezone(UTC)).total_seconds()
    if not (0 <= age <= 120
            and broker.get("login") == int(config["MT5_ACCOUNT3_LOGIN"])
            and broker.get("mode") == config["MT5_ACCOUNT3_MODE"] == "REAL"
            and broker.get("currency") == config["MT5_ACCOUNT3_CURRENCY"] == "USD"
            and broker.get("server") == config["MT5_ACCOUNT3_SERVER"]
            and broker.get("feed_http") == 200
            and broker.get("broker_positions_protected") is True
            and broker.get("unmanaged_positions_count") == 0
            and broker.get("unmanaged_orders_count") == 0
            and type(broker.get("broker_positions_total")) is int
            and type(broker.get("broker_orders_total")) is int):
        raise ValueError("Account3 identity/feed/broker stops are not verified")
    return {"account3": "broker-verified", "feed_http": 200,
            "telemetry_age_seconds": round(age),
            "boot_enabled": {u: call("systemctl", "is-enabled", u) == "enabled" for u in UNITS}}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-boot", action="store_true")
    args = parser.parse_args()
    try:
        result = check()
        print(json.dumps(result, indent=2))
        if args.enable_boot:
            if call("git", "-C", "/opt/dkcapital", "branch", "--show-current") != "main":
                raise ValueError("must use reviewed main branch")
            call("systemctl", "enable", *UNITS)
            print("Account3 MT5 and VNC enabled for NEXT BOOT; no live service restarted.")
        else:
            print("READ-ONLY AUDIT. No systemd changes.")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"BLOCKED: {type(error).__name__}: {error}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
