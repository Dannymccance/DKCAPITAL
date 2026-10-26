#!/usr/bin/env python3
"""Read-only authenticated MT5 broker gateway acceptance for three accounts.

Never places orders, changes EAs, restarts services or prints bearer tokens.
A 200 feed is connectivity proof, not live execution or recovery certification.
"""
from __future__ import annotations
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ENV = Path("/opt/dkcapital/.env")
BASE = "http://127.0.0.1:8765"
ALLOWED = ("MT5_GATEWAY_TOKEN", "MT5_ACCOUNT1_LOGIN",
           *(f"MT5_ACCOUNT{n}_{field}" for n in (2, 3)
             for field in ("LOGIN", "MODE", "CURRENCY", "SERVER", "TOKEN")))


def private_values(path: Path = ENV) -> dict[str, str]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1048576:
        raise ValueError("VPS private environment missing, symlinked or oversized")
    found = {}
    for row in path.read_text(encoding="utf-8").splitlines():
        if "=" not in row or row.lstrip().startswith("#"):
            continue
        key, value = row.split("=", 1)
        key = key.strip()
        if key in ALLOWED:
            if key in found:
                raise ValueError("duplicate private environment key: " + key)
            found[key] = value.strip().strip('"').strip("'")
    for key in ALLOWED:
        if not found.get(key):
            raise ValueError("missing required broker profile configuration: " + key)
    tokens = [found["MT5_GATEWAY_TOKEN"],
              found["MT5_ACCOUNT2_TOKEN"], found["MT5_ACCOUNT3_TOKEN"]]
    if len(tokens) != len(set(tokens)) or any(len(t) < 24 for t in tokens):
        raise ValueError("missing or duplicate independent gateway tokens")
    for n in (1, 2, 3):
        name = "MT5_ACCOUNT1_LOGIN" if n == 1 else f"MT5_ACCOUNT{n}_LOGIN"
        if not found[name].isdigit() or int(found[name]) <= 0:
            raise ValueError("invalid " + name)
    logins = [int(found["MT5_ACCOUNT1_LOGIN"]),
              int(found["MT5_ACCOUNT2_LOGIN"]), int(found["MT5_ACCOUNT3_LOGIN"])]
    if len(logins) != len(set(logins)):
        raise ValueError("broker logins are not unique")
    return found


def read_url(path: str, token: str) -> tuple[int, bytes]:
    if not path.startswith("/") or re.search(r"[^a-zA-Z0-9/_-]", path):
        raise ValueError("invalid local gateway endpoint")
    req = Request(BASE + path, headers={"Authorization": "Bearer " + token})
    try:
        with urlopen(req, timeout=8) as response:
            return response.status, response.read(1024 * 1024)
    except HTTPError as err:
        return err.code, err.read(1024)


def is_active(unit: str) -> bool:
    p = subprocess.run(["systemctl", "is-active", unit],
                       capture_output=True, text=True, timeout=6, check=False)
    return p.returncode == 0 and p.stdout.strip() == "active"


def validate_snapshot(data: dict, n: int, env: dict, *, now: datetime,
                      expect_account2_entries: bool = False) -> list[str]:
    issues = []
    expected_login = int(env["MT5_ACCOUNT1_LOGIN"] if n == 1
                         else env[f"MT5_ACCOUNT{n}_LOGIN"])
    if data.get("login") != expected_login:
        issues.append("wrong login")
    expected_mode = "DEMO" if n == 1 else env[f"MT5_ACCOUNT{n}_MODE"]
    expected_currency = "GBP" if n == 1 else env[f"MT5_ACCOUNT{n}_CURRENCY"]
    if data.get("mode") != expected_mode or data.get("currency") != expected_currency:
        issues.append("wrong mode/currency")
    if n != 1 and data.get("server") != env[f"MT5_ACCOUNT{n}_SERVER"]:
        issues.append("wrong broker server")
    try:
        stamp = datetime.fromisoformat(str(data["received_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("missing timezone")
        age = (now - stamp.astimezone(UTC)).total_seconds()
        if age < -5 or age > 150:
            issues.append("stale telemetry")
    except (ValueError, TypeError, KeyError):
        issues.append("invalid telemetry timestamp")
    if data.get("feed_http") != 200:
        issues.append("EA reports feed HTTP != 200")
    if data.get("signal_risk_cap_pct") != 4.0:
        issues.append("standard risk policy is not 4%")
    if data.get("daily_loss_guard_mode") != "ALERT_ONLY" or data.get("daily_loss_warning_pct") != 2.0:
        issues.append("daily loss policy is not 2% alert-only")
    if n == 2 and data.get("entry_enabled") is not expect_account2_entries:
        issues.append("Kyle EA entry state does not match acceptance expectation")
    for key in ("broker_positions_total", "broker_orders_total"):
        if type(data.get(key)) is not int or data[key] < 0:
            issues.append("invalid " + key)
    if data.get("broker_positions_protected") is not True:
        issues.append("broker-native stops not verified")
    if n in (2, 3):
        risk = data.get("signal_risk_cap_account_currency")
        balance = data.get("balance")
        pct = data.get("signal_risk_cap_pct")
        if not all(type(x) in (int, float) for x in (risk, balance, pct)):
            issues.append("missing signal risk report")
        elif balance <= 0 or risk > balance * pct / 100 + 0.03:
            issues.append("risk cap exceeds reported cash-balance percentage")
    return issues


def assess(env: dict) -> dict:
    now = datetime.now(UTC)
    result = {"checked_at": now.isoformat(), "accounts": {}, "overall": "PASS"}
    for n in (1, 2, 3):
        token = env["MT5_GATEWAY_TOKEN"] if n == 1 else env[f"MT5_ACCOUNT{n}_TOKEN"]
        feed = "/v5/demo/feed" if n == 1 else f"/v5/accounts/{n}/feed"
        snapshot_url = "/v2/demo/account1" if n == 1 else f"/v5/accounts/{n}/snapshot"
        expected_protocol = "DKDEMO5" if n == 1 else f"DKACCOUNT{n}V5"
        fstatus, payload = read_url(feed, token)
        status, body = read_url(snapshot_url, token)
        issues = []
        if fstatus != 200 or payload.splitlines()[0:1] != [expected_protocol.encode()]:
            issues.append("feed not HTTP200 with expected protocol")
        if status != 200:
            issues.append(f"snapshot HTTP {status}")
            data = {}
        else:
            try:
                data = json.loads(body)
                issues += validate_snapshot(data, n, env, now=now)
            except (ValueError, TypeError):
                issues.append("invalid broker snapshot JSON")
                data = {}
        # A distinct-account token MUST NOT authenticate to this account.
        other = env["MT5_ACCOUNT3_TOKEN"] if n != 3 else env["MT5_ACCOUNT2_TOKEN"]
        other_status, _ = read_url(feed, other)
        if other_status != 401:
            issues.append("CROSS-ACCOUNT TOKEN ISOLATION FAILED")
        mt5 = is_active(f"dkcapital-mt5-{n}.service")
        if not mt5:
            issues.append("MT5 systemd service inactive")
        result["accounts"][str(n)] = {
            "status": "PASS" if not issues else "FAIL",
            "issues": issues,
            "feed_http": fstatus,
            "snapshot_http": status,
            "other_account_token_http": other_status,
            "mt5_active": mt5,
            "entry_enabled": data.get("entry_enabled"),
            "broker_positions_total": data.get("broker_positions_total"),
            "broker_orders_total": data.get("broker_orders_total"),
        }
        if issues:
            result["overall"] = "FAIL"
    result["limitations"] = (
        "Connectivity, identity and broker-snapshot checks only; no live fills, "
        "SL amendments, TP execution, terminal restart or post-restart "
        "reconciliation tested."
    )
    return result


def main() -> int:
    try:
        report = assess(private_values())
    except (OSError, ValueError, KeyError, URLError, subprocess.SubprocessError) as exc:
        print(f"BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
