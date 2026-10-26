#!/usr/bin/env python3
"""Generate distinct, initially disarmed MT5 EA source for Kyle or Dan.

This does not attach an EA, place a trade, read account passwords or modify
the existing GBP DEMO EA. Broker identity must be verified independently.
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "mt5/DKcapitalV2.mq5"


def generate(account_id: int, login: int, mode: str, currency: str,
             server: str, lot_denominator: float, feed_token: str | None = None) -> str:
    if account_id not in (2, 3):
        raise ValueError("Only independent profile 2 or 3 can use this generator")
    if type(login) is not int or login <= 0 or login == 5057220041:
        raise ValueError("Actual separately verified broker login required")
    if mode not in ("DEMO", "REAL") or currency not in ("GBP", "USD"):
        raise ValueError("Explicit broker mode and GBP/USD currency required")
    if (not isinstance(server, str)
            or not re.fullmatch(r"[A-Za-z0-9._ -]{3,100}", server)
            or server != server.strip()):
        raise ValueError("Exact MT5 broker server required")
    if (not math.isfinite(lot_denominator) or not 50 <= lot_denominator <= 10000):
        raise ValueError("Explicit approved account-currency lot denominator required")

    if feed_token is not None:
        if account_id != 3 or re.fullmatch(r"[0-9a-fA-F]{64}", feed_token) is None:
            raise ValueError("Private embedded token is supported for Account 3 and 64 hex characters only")

    source = TEMPLATE.read_text(encoding="utf-8")

    def replace(old: str, new: str, minimum: int = 1) -> None:
        nonlocal source
        hits = source.count(old)
        if hits < minimum:
            raise RuntimeError(f"Changed EA template: absent marker {old!r}")
        source = source.replace(old, new)

    if feed_token is not None:
        replace('input string FeedToken = "";', f'input string FeedToken = "{feed_token}";')

    replace("// DKcapital V2.11: DEMO-only GBP Account 1",
            f"// DKcapital V2.11: profile {account_id}, verified {mode} {currency} account")
    replace('input string FeedUrl = "http://127.0.0.1:8765/v5/demo/feed";',
            f'input string FeedUrl = "http://127.0.0.1:8765/v5/accounts/{account_id}/feed";')
    replace('input string TelemetryUrl = "http://127.0.0.1:8765/v2/demo/telemetry";',
            f'input string TelemetryUrl = "http://127.0.0.1:8765/v5/accounts/{account_id}/telemetry";')
    replace('input long ExpectedDemoLogin = 0;',
            f'const int COMPILED_ACCOUNT_ID = {account_id};\n'
            f'input long ExpectedAccountLogin = {login};')
    replace("ExpectedDemoLogin", "ExpectedAccountLogin")
    replace("DemoIdentity", "ProfileIdentity")
    replace("ACCOUNT_TRADE_MODE_DEMO",
            "ACCOUNT_TRADE_MODE_DEMO" if mode == "DEMO" else "ACCOUNT_TRADE_MODE_REAL")
    replace('AccountInfoString(ACCOUNT_CURRENCY)=="GBP";',
            f'AccountInfoString(ACCOUNT_CURRENCY)=="{currency}" &&\n'
            f'       AccountInfoString(ACCOUNT_SERVER)=="{server}";')
    replace('StringFind(lines[0],"DKDEMO5")',
            f'StringFind(lines[0],"DKACCOUNT{account_id}V5")')
    replace('StringFind(FeedUrl,"http://127.0.0.1:8765/")!=0',
            f'FeedUrl!="http://127.0.0.1:8765/v5/accounts/{account_id}/feed"')
    replace('StringFind(TelemetryUrl,"http://127.0.0.1:8765/")!=0',
            f'TelemetryUrl!="http://127.0.0.1:8765/v5/accounts/{account_id}/telemetry"')
    # GBP-labelled functions and fields must not be represented as GBP on a USD account.
    for old, new in (
        ("RiskGBP", "RiskAccountCurrency"),
        ("RiskBalanceGBP", "RiskBalanceAccountCurrency"),
        ("SignalRiskLimitGBP", "SignalRiskLimitAccountCurrency"),
        ("GroupRiskLimitGBP", "GroupRiskLimitAccountCurrency"),
        ("PortfolioRiskLimitGBP", "PortfolioRiskLimitAccountCurrency"),
        ("RiskReferenceBalanceGBP", "RiskReferenceBalanceAccountCurrency"),
        ("RiskPaddingGBPPerLeg", "RiskPaddingAccountCurrencyPerLeg"),
        ("signal_risk_cap_gbp", "signal_risk_cap_account_currency"),
        ("risk_gbp", "risk_account_currency"),
    ):
        replace(old, new)
    replace("MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/100)*0.01",
            "MathFloor(AccountInfoDouble(ACCOUNT_BALANCE)/LotDenominatorAccountCurrency)*0.01")
    replace("input double HighRiskSignalPct = 2.0;",
            "input double HighRiskSignalPct = 2.0;\n"
            f"input double LotDenominatorAccountCurrency = {lot_denominator:.4f};")
    replace("HighRiskSignalPct<=0 || HighRiskSignalPct>MaxSignalRiskPct ||",
            "HighRiskSignalPct<=0 || HighRiskSignalPct>MaxSignalRiskPct ||\n"
            "      LotDenominatorAccountCurrency<=0 ||")
    replace("return MathMin(balance,equity);",
            "double credit=AccountInfoDouble(ACCOUNT_CREDIT);\n"
            "   if(credit<0 || !MathIsValidNumber(credit)) return 0;\n"
            "   // Broker credit / promotional bonus never funds risk.\n"
            "   return MathMin(balance,MathMax(0.0,equity-credit));")
    replace(r'\"mode\":\"DEMO\",\"currency\":\"GBP\"',
            rf'\"mode\":\"{mode}\",\"currency\":\"{currency}\"')
    # Independent profile telemetry is rejected unless it reports the exact broker
    # server, not just the login/mode/currency. Send the live terminal value only
    # after ProfileIdentity() has proved it matches the compiled server.
    replace(
        r'json+="\"login\":"+L(ExpectedAccountLogin)+',
        r'json+="\"server\":\""+AccountInfoString(ACCOUNT_SERVER)+"\",";'
        + "\n   " + r'json+="\"login\":"+L(ExpectedAccountLogin)+'
    )
    replace('DK2 BLOCK requires approved GBP DEMO hedging login',
            f'DK2 BLOCK requires verified {mode} {currency} hedging profile {account_id}')
    replace("DK2 DEMO Account 1 ready", f"DK2 {mode} Account {account_id} ready")
    # No profile is armed by source generation. Default remains false.
    if "input bool EnableNewEntries = false;" not in source:
        raise RuntimeError("Candidate lost safe disarmed default")
    if (f"DKACCOUNT{account_id}V5" not in source
            or f"COMPILED_ACCOUNT_ID = {account_id}" not in source):
        raise RuntimeError("Candidate missing account isolation")
    return source



def token_from_private_env(path: Path, *, login: int, mode: str,
                           currency: str, server: str) -> str:
    """Load a private token locally; never print it or include it in Git commits."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Private .env file missing, symlinked, or oversized")
    expected = {
        "MT5_ACCOUNT3_LOGIN": str(login),
        "MT5_ACCOUNT3_MODE": mode,
        "MT5_ACCOUNT3_CURRENCY": currency,
        "MT5_ACCOUNT3_SERVER": server,
    }
    seen: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in (*expected, "MT5_ACCOUNT3_TOKEN"):
            if key in seen:
                raise ValueError(f"Duplicate {key} in private environment")
            seen[key] = value.strip()
    for key, value in expected.items():
        if seen.get(key) != value:
            raise ValueError(f"Account 3 private environment identity mismatch: {key}")
    token = seen.get("MT5_ACCOUNT3_TOKEN", "")
    if re.fullmatch(r"[0-9a-fA-F]{64}", token) is None:
        raise ValueError("MT5_ACCOUNT3_TOKEN must be a 64-character hexadecimal value")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", type=int, choices=(2, 3), required=True)
    parser.add_argument("--login", type=int, required=True)
    parser.add_argument("--mode", choices=("DEMO", "REAL"), required=True)
    parser.add_argument("--currency", choices=("GBP", "USD"), required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--lot-denominator", type=float, required=True,
                        help="Manually approved account-currency units per 0.01 lots")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--feed-token-env-file", type=Path,
                        help="For Account 3 only: privately prefill the EA token from local VPS .env")
    args = parser.parse_args()
    token = None
    if args.feed_token_env_file is not None:
        if args.account_id != 3:
            parser.error("Private token preset is Account 3-only")
        try:
            token = token_from_private_env(args.feed_token_env_file, login=args.login,
                                           mode=args.mode, currency=args.currency,
                                           server=args.server)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    candidate = generate(args.account_id, args.login, args.mode,
                         args.currency, args.server, args.lot_denominator,
                         feed_token=token)
    if args.output_dir.is_symlink() or not args.output_dir.is_dir():
        parser.error("Use an existing real isolated compiler staging directory")
    suffix = "_LocalToken" if token is not None else ""
    filename = f"DKcapitalV211_Account{args.account_id}_{args.mode}_{args.currency}{suffix}.mq5"
    path = args.output_dir / filename
    if path.exists() or path.is_symlink():
        parser.error("Candidate exists: no overwrites of reviewed sources")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(candidate)
    path.chmod(0o600)
    print(f"STAGED {path} (NOT compiled, attached, approved or armed)")
    if token is not None:
        print("Private token preset in local source only; do NOT commit/share the MQ5 or EX5")


if __name__ == "__main__":
    main()
