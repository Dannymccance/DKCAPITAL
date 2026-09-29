from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dkcapital.config import Settings
from dkcapital.event_store import JsonlEventStore
from dkcapital.logging_setup import configure_logging
from dkcapital.market_data import GoldSpotClient
from dkcapital.paper_trading import PaperAccount
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

logger = logging.getLogger("dkcapital.paper_engine")


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _load_account(settings: Settings) -> tuple[PaperAccount, bool]:
    existing = _load_json(settings.paper_state_path)
    if existing:
        account = PaperAccount.from_snapshot(existing)
        if abs(account.starting_balance_usd - settings.paper_starting_balance_usd) > 1e-9:
            logger.warning(
                "Paper state starting balance %.2f differs from config %.2f; "
                "preserving durable state",
                account.starting_balance_usd,
                settings.paper_starting_balance_usd,
            )
        account.entry_policy = settings.paper_entry_policy
        account.activate_strategy(settings.paper_strategy_mode)
        return account, False

    account = PaperAccount(
        starting_balance_usd=settings.paper_starting_balance_usd,
        symbol=settings.paper_symbol,
        strategy_mode=settings.paper_strategy_mode,
        entry_policy=settings.paper_entry_policy,
    )
    account.activate_strategy(settings.paper_strategy_mode)
    return account, True


def _append_audit(
    event_store: JsonlEventStore,
    *,
    kind: str,
    data: dict[str, Any],
) -> None:
    event_store.append(
        {
            "event_type": kind,
            "observed_at": datetime.now(UTC).isoformat(),
            **data,
        }
    )


def _sync_signals(
    account: PaperAccount,
    signal_state: dict[str, Any],
    *,
    initial_snapshot: bool,
    event_store: JsonlEventStore,
) -> int:
    changed_count = 0
    for signal in signal_state.get("signals") or []:
        changed, result = account.sync_signal(
            signal,
            initial_snapshot=initial_snapshot,
        )
        if not changed:
            continue
        changed_count += 1
        _append_audit(
            event_store,
            kind="paper_signal_sync",
            data={
                "signal_id": signal.get("signal_id"),
                "symbol": signal.get("symbol"),
                "direction": signal.get("direction"),
                "result": result,
                "initial_snapshot": initial_snapshot,
                "strategy_mode": account.strategy_mode,
                "entry_policy": account.entry_policy,
            },
        )
    return changed_count


def _quote_key(quote: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if quote is None:
        return None
    return (
        quote.get("bid"),
        quote.get("ask"),
        quote.get("price"),
        quote.get("computed_at"),
    )


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    account, first_start = _load_account(settings)
    event_store = JsonlEventStore(settings.paper_event_log_path)
    gold_spot = GoldSpotClient(
        settings.gold_spot_url,
        settings.paper_mark_refresh_seconds,
    )
    strategy = XauSignalFollowingStrategy(
        XauStrategyConfig(
            risk_pct=settings.paper_risk_pct,
            direction_risk_cap_pct=settings.paper_direction_risk_cap_pct,
            min_trade_risk_pct=settings.paper_min_trade_risk_pct,
            daily_loss_pct=settings.paper_daily_loss_pct,
            contract_oz_per_lot=settings.paper_xau_contract_oz_per_lot,
            lot_step=settings.paper_xau_lot_step,
            timezone_name=settings.display_timezone,
        )
    )

    signal_state = _load_json(settings.signal_state_path)
    synced = _sync_signals(
        account,
        signal_state,
        initial_snapshot=first_start,
        event_store=event_store,
    )

    # Force one fresh quote on boot. Existing signals that predate strategy
    # activation are recorded but intentionally not opened as stale trades.
    quote = await gold_spot.quote(force=True)
    if quote is not None and account.strategy_mode != "observe_only":
        strategy.process(account, quote)
    elif quote is not None and quote.get("price") is not None:
        price = float(quote["price"])
        account.mark_quote(
            bid=float(quote.get("bid") or price),
            ask=float(quote.get("ask") or price),
            price=price,
            marked_at=str(quote.get("computed_at") or datetime.now(UTC).isoformat()),
        )

    _write_json(settings.paper_state_path, account.snapshot())

    logger.info(
        "Paper engine ready balance=%.2f symbol=%s mode=%s entry_policy=%s "
        "risk=%.2f%% direction_cap=%.2f%% daily_stop=%.2f%% candidates=%s "
        "positions=%s initial_sync=%s",
        account.balance_usd,
        account.symbol,
        account.strategy_mode,
        account.entry_policy,
        settings.paper_risk_pct * 100.0,
        settings.paper_direction_risk_cap_pct * 100.0,
        settings.paper_daily_loss_pct * 100.0,
        len(account.candidates),
        len(account.positions),
        synced,
    )

    last_signal_mtime_ns = (
        settings.signal_state_path.stat().st_mtime_ns
        if settings.signal_state_path.exists()
        else 0
    )
    last_quote_key = _quote_key(quote)

    while True:
        state_changed = False
        signal_changed = False

        if settings.signal_state_path.exists():
            current_mtime_ns = settings.signal_state_path.stat().st_mtime_ns
            if current_mtime_ns != last_signal_mtime_ns:
                signal_state = _load_json(settings.signal_state_path)
                synced = _sync_signals(
                    account,
                    signal_state,
                    initial_snapshot=False,
                    event_store=event_store,
                )
                if synced:
                    logger.info(
                        "Paper engine synced XAUUSD signals changed=%s candidates=%s",
                        synced,
                        len(account.candidates),
                    )
                    state_changed = True
                    signal_changed = True
                last_signal_mtime_ns = current_mtime_ns

        # A new Telegram signal/update gets a fresh quote immediately. Between
        # signal events the quote client uses its configured cache interval.
        quote = await gold_spot.quote(force=signal_changed)
        current_quote_key = _quote_key(quote)
        if current_quote_key != last_quote_key:
            state_changed = True
            last_quote_key = current_quote_key

        if quote is not None:
            if account.strategy_mode != "observe_only":
                state_changed = strategy.process(account, quote) or state_changed
            elif quote.get("price") is not None:
                price = float(quote["price"])
                account.mark_quote(
                    bid=float(quote.get("bid") or price),
                    ask=float(quote.get("ask") or price),
                    price=price,
                    marked_at=str(
                        quote.get("computed_at") or datetime.now(UTC).isoformat()
                    ),
                )

        if state_changed:
            _write_json(settings.paper_state_path, account.snapshot())

        await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
