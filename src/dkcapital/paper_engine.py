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
        return account, False

    return (
        PaperAccount(
            starting_balance_usd=settings.paper_starting_balance_usd,
            symbol=settings.paper_symbol,
            strategy_mode=settings.paper_strategy_mode,
            entry_policy=settings.paper_entry_policy,
        ),
        True,
    )


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
            },
        )
    return changed_count


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    account, first_start = _load_account(settings)
    event_store = JsonlEventStore(settings.paper_event_log_path)
    gold_spot = GoldSpotClient(
        settings.gold_spot_url,
        settings.paper_mark_refresh_seconds,
    )

    signal_state = _load_json(settings.signal_state_path)
    synced = _sync_signals(
        account,
        signal_state,
        initial_snapshot=first_start,
        event_store=event_store,
    )
    _write_json(settings.paper_state_path, account.snapshot())

    logger.info(
        "Paper engine ready balance=%.2f symbol=%s mode=%s candidates=%s "
        "positions=%s initial_sync=%s",
        account.balance_usd,
        account.symbol,
        account.strategy_mode,
        len(account.candidates),
        len(account.positions),
        synced,
    )

    last_signal_mtime_ns = (
        settings.signal_state_path.stat().st_mtime_ns
        if settings.signal_state_path.exists()
        else 0
    )

    while True:
        state_changed = False

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
                last_signal_mtime_ns = current_mtime_ns

        quote = await gold_spot.quote()
        if quote is not None and quote.get("price") is not None:
            price = float(quote["price"])
            if account.last_mark_price != price:
                account.mark(
                    price,
                    marked_at=str(quote.get("computed_at") or datetime.now(UTC).isoformat()),
                )
                state_changed = True

        if state_changed:
            _write_json(settings.paper_state_path, account.snapshot())

        await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
