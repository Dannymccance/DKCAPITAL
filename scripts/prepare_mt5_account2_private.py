#!/usr/bin/env python3
"""Prepare Kyle's token-prefilled Account2 V2.11 EA without modifying his terminal.

Broker identity/token MUST already exist in the VPS .env. Never handles MT5
passwords. Broker symbol and currency-normalised lot denominator are explicit.
Generated MQ5 contains a secret: do not commit, paste, or upload it to GitHub.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from generate_mt5_v211_profile import generate, token_from_private_env

DEFAULT_ENV = Path("/opt/dkcapital/.env")
DEFAULT_OUT = Path("/opt/dkcapital-mt5-compiler/workspace/MetaTrader 5/MQL5/Experts")

def private_identity(path: Path) -> dict[str, str]:
    required = {f"MT5_ACCOUNT2_{key}" for key in ("LOGIN","MODE","CURRENCY","SERVER","TOKEN")}
    found: dict[str, str] = {}
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Private .env missing, symlinked or oversized")
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in required:
            if key in found:
                raise ValueError("Duplicate private account2 field: " + key)
            found[key] = value.strip()
    if set(found) != required or not all(found.values()):
        raise ValueError("Kyle's five broker identity/token fields must be configured in private .env")
    return found


def prepare(env_file: Path, output_dir: Path, lot_denominator: float, symbol: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._+-]{2,32}", symbol):
        raise ValueError("Invalid broker gold symbol")
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("Use an existing isolated compiler Experts directory")
    cfg = private_identity(env_file)
    login = int(cfg["MT5_ACCOUNT2_LOGIN"])
    mode = cfg["MT5_ACCOUNT2_MODE"]
    currency = cfg["MT5_ACCOUNT2_CURRENCY"]
    server = cfg["MT5_ACCOUNT2_SERVER"]
    token = token_from_private_env(
        env_file, login=login, mode=mode, currency=currency,
        server=server, account_id=2)
    source = generate(2, login, mode, currency, server, lot_denominator,
                      feed_token=token)
    if symbol != "XAUUSD":
        expected = 'input string GoldSymbol = "XAUUSD";'
        if source.count(expected) != 1:
            raise ValueError("EA symbol input marker changed")
        source = source.replace(expected, f'input string GoldSymbol = "{symbol}";')
    name = f"DKcapitalV211_Account2_{mode}_{currency}_Private.mq5"
    path = output_dir / name
    if path.exists() or path.is_symlink():
        raise ValueError("Refusing to overwrite an existing source")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(source)
    path.chmod(0o600)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--lot-denominator", required=True, type=float)
    parser.add_argument("--symbol", required=True,
                        help="Exact broker gold symbol as seen in Kyle's Market Watch")
    args = parser.parse_args()
    try:
        result = prepare(args.env_file, args.output_dir, args.lot_denominator, args.symbol)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print("Generated Account2 token-prefilled EA source:", result)
    print("SAFE DEFAULT: EnableNewEntries=false and EnableLayerEntries=false")
    print("Not compiled, installed, attached or armed; do not commit/share this MQ5.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
