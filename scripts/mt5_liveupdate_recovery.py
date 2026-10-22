#!/usr/bin/env python3
"""Conservative demo-only recovery for MT5 LiveUpdate exit 78.

This does not replace the existing frozen-telemetry autoheal.
Live accounts, existing exposure, and unapproved binaries fail closed.
"""
import argparse
import fcntl
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import time

CONFIG = Path("/etc/dkcapital/mt5-liveupdate-recovery.json")
STATE = Path("/var/lib/dkcapital/mt5-liveupdate-recovery")


def command(*args, timeout=15):
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def failure_is_liveupdate(service):
    props = command("systemctl", "show", service, "-p", "ActiveState", "-p", "ExecMainStatus")
    values = dict(line.split("=", 1) for line in props.stdout.splitlines() if "=" in line)
    if props.returncode or values.get("ActiveState") != "failed" or values.get("ExecMainStatus") != "78":
        return False
    logs = command("journalctl", "-u", service, "-n", "150", "--no-pager", "-o", "cat")
    if logs.returncode:
        return False
    lines = logs.stdout.splitlines()
    starts = [i for i, s in enumerate(lines) if "MT5 START:" in s]
    recent = lines[starts[-1]:] if starts else []
    return any("LiveUpdate attempted" in s for s in recent)


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as src:
        for chunk in iter(lambda: src.read(1048576), b""):
            sha.update(chunk)
    return sha.hexdigest()


def safe_demo_snapshot(snapshot, login, now, max_age=600):
    if snapshot.get("mode") != "DEMO" or str(snapshot.get("login")) != str(login):
        return False
    if any(type(snapshot.get(k)) is not int or snapshot[k] != 0 for k in
           ("broker_positions_total", "broker_orders_total", "unmanaged_positions_count")):
        return False
    if snapshot.get("unmanaged_orders_count", 0) != 0:
        return False
    if snapshot.get("positions") != [] or snapshot.get("orders", []) != []:
        return False
    try:
        stamp = datetime.fromisoformat(snapshot["generated_at"].replace("Z", "+00:00"))
        age = now - stamp.timestamp()
        return stamp.tzinfo is not None and 0 <= age <= max_age
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def recover(account, apply=False):
    ident = str(account["id"])
    if not account.get("enabled", False):
        return "DISABLED"
    service = account["service"]
    if not re.fullmatch(r"dkcapital-mt5-[1-9][0-9]*\\.service", service):
        return "BLOCKED: invalid service"
    if command("systemctl", "is-active", "--quiet", service).returncode == 0:
        return "HEALTHY"
    if not account.get("demo_recovery", False) or not failure_is_liveupdate(service):
        return "BLOCKED: no eligible LiveUpdate failure"
    try:
        snapshot = json.loads(Path(account["telemetry"]).read_text())
        if not safe_demo_snapshot(snapshot, account["login"], time.time()):
            return "BLOCKED: demo exposure snapshot incomplete, stale, or nonzero"
        expected = Path(account["approval"]).read_text().strip()
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
            return "BLOCKED: approval invalid"
        if digest(Path(account["executable"])) != expected.lower():
            return "BLOCKED: executable changed"
        staging = Path(account["liveupdate"])
        if not staging.is_dir() or staging.is_symlink():
            return "BLOCKED: staging unavailable"
    except (OSError, ValueError, KeyError):
        return "BLOCKED: required evidence missing"
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / (ident + ".lock")).open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "BUSY"
        marker = STATE / (ident + ".last-attempt")
        try:
            last = float(marker.read_text())
        except (OSError, ValueError):
            last = 0
        now = time.time()
        if now - last < 86400:
            return "COOLDOWN"
        if not apply:
            return "DRY_RUN: eligible"
        # Refuse staging currently being modified.
        try:
            if any(now - p.stat().st_mtime < 120 for p in staging.rglob("*") if p.is_file()):
                return "BLOCKED: staging recently modified"
        except OSError:
            return "BLOCKED: staging unreadable"
        # Re-evaluate failure immediately before mutation.
        if not failure_is_liveupdate(service):
            return "BLOCKED: service state changed"
        marker.write_text(str(now))
        target = staging.with_name("liveupdate-quarantine-" +
                                   datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        staging.rename(target)
        for operation in ("reset-failed", "start"):
            result = command("systemctl", operation, service, timeout=45)
            if result.returncode:
                return "FAILED: " + operation
        return "PROCESS_STARTED: requires independent fresh telemetry validation"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    opts = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    for account in config["accounts"]:
        print(json.dumps({"account": account["id"], "result": recover(account, opts.apply)}))


if __name__ == "__main__":
    main()
