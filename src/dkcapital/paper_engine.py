from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dkcapital.config import Settings
from dkcapital.event_store import JsonlEventStore
from dkcapital.logging_setup import configure_logging
from dkcapital.market_data import GoldSpotClient
from dkcapital.paper_trading import PaperAccount
from dkcapital.signal_state import SignalState
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

logger = logging.getLogger("dkcapital.paper_engine")

DELAY_STATE_VERSION = 1


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


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        handle.flush()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _delay_cutoff(
    delay_seconds: int,
    *,
    now: datetime | None = None,
) -> datetime:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return current - timedelta(seconds=max(0, int(delay_seconds)))


def _telegram_time(event: dict[str, Any]) -> datetime:
    for value in (
        event.get("observed_at"),
        event.get("edit_date"),
        event.get("date"),
    ):
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def _market_time(row: dict[str, Any]) -> datetime:
    for value in (
        row.get("captured_at"),
        row.get("computed_at"),
    ):
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _peek_jsonl(
    path: Path,
    offset: int,
) -> tuple[dict[str, Any], int] | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, int(offset)))
            raw = handle.readline()
            if not raw or not raw.endswith(b"\n"):
                return None
            next_offset = handle.tell()
    except OSError:
        return None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Skipping malformed JSONL row path=%s offset=%s", path, offset)
        return {"event_type": "_malformed", "observed_at": datetime.min.replace(tzinfo=UTC).isoformat()}, next_offset

    if not isinstance(payload, dict):
        return {"event_type": "_malformed", "observed_at": datetime.min.replace(tzinfo=UTC).isoformat()}, next_offset
    return payload, next_offset


def _iter_jsonl_until(
    path: Path,
    end_offset: int,
) -> list[tuple[dict[str, Any], int]]:
    if not path.exists() or end_offset <= 0:
        return []

    rows: list[tuple[dict[str, Any], int]] = []
    try:
        with path.open("rb") as handle:
            while handle.tell() < end_offset:
                raw = handle.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                next_offset = handle.tell()
                if next_offset > end_offset:
                    break
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict):
                    rows.append((payload, next_offset))
    except OSError:
        return []
    return rows


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
    observed_at: str | None = None,
) -> int:
    changed_count = 0
    for signal in signal_state.get("signals") or []:
        changed, result = account.sync_signal(
            signal,
            initial_snapshot=initial_snapshot,
            observed_at=observed_at,
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
                "delayed_simulation": True,
                "simulation_observed_at": observed_at,
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


def _load_delay_state(settings: Settings) -> tuple[dict[str, Any], bool]:
    existing = _load_json(settings.paper_delay_state_path)
    if existing and int(existing.get("version", 0)) == DELAY_STATE_VERSION:
        return existing, False

    now = datetime.now(UTC).isoformat()
    telegram_size = _file_size(settings.telegram_event_log_path)
    market_size = _file_size(settings.paper_market_tape_path)
    state = {
        "version": DELAY_STATE_VERSION,
        "activated_at": now,
        "baseline_telegram_offset": telegram_size,
        "telegram_offset": telegram_size,
        "market_offset": market_size,
        "last_telegram_processed_at": None,
        "last_market_processed_at": None,
    }
    _write_json(settings.paper_delay_state_path, state)
    return state, True


def _build_delayed_signal_state(
    settings: Settings,
    delay_state: dict[str, Any],
) -> SignalState:
    state = SignalState()
    processed_offset = int(delay_state.get("telegram_offset", 0) or 0)
    baseline_offset = int(
        delay_state.get("baseline_telegram_offset", processed_offset) or 0
    )

    for event, next_offset in _iter_jsonl_until(
        settings.telegram_event_log_path,
        processed_offset,
    ):
        if (
            next_offset > baseline_offset
            and str(event.get("event_type") or "") == "historical_message"
        ):
            continue
        state.ingest_event(event, rebuild=False)

    state.rebuild()
    return state


def _strategy(settings: Settings) -> XauSignalFollowingStrategy:
    return XauSignalFollowingStrategy(
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


def _paper_snapshot(
    account: PaperAccount,
    settings: Settings,
    delay_state: dict[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    payload = account.snapshot()
    cutoff = _delay_cutoff(settings.paper_simulation_delay_seconds, now=now)
    activated = _parse_dt(delay_state.get("activated_at"))
    buffered_seconds = (
        max(0.0, (now - activated).total_seconds())
        if activated is not None
        else 0.0
    )
    warming_up = buffered_seconds < settings.paper_simulation_delay_seconds

    payload["simulation"] = {
        "enabled": True,
        "delay_seconds": settings.paper_simulation_delay_seconds,
        "delay_minutes": settings.paper_simulation_delay_seconds / 60.0,
        "wall_time": now.isoformat(),
        "simulation_time": cutoff.isoformat(),
        "warming_up": warming_up,
        "buffered_seconds": min(
            buffered_seconds,
            float(settings.paper_simulation_delay_seconds),
        ),
        "market_source": "standard-bullion:XAUUSD",
        "market_tape_path": str(settings.paper_market_tape_path),
        "last_market_processed_at": delay_state.get("last_market_processed_at"),
        "last_telegram_processed_at": delay_state.get("last_telegram_processed_at"),
    }
    return payload


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    account, first_start = _load_account(settings)
    event_store = JsonlEventStore(settings.paper_event_log_path)
    gold_spot = GoldSpotClient(
        settings.gold_spot_url,
        settings.paper_mark_refresh_seconds,
    )
    strategy = _strategy(settings)
    delay_state, delay_initialized = _load_delay_state(settings)

    telegram_size = _file_size(settings.telegram_event_log_path)
    if telegram_size < int(delay_state.get("telegram_offset", 0) or 0):
        logger.warning(
            "Telegram event log shrank below delayed cursor; rebasing delayed "
            "Telegram state to current durable account baseline"
        )
        delay_state["baseline_telegram_offset"] = telegram_size
        delay_state["telegram_offset"] = telegram_size
        delay_state["last_telegram_processed_at"] = None
        _write_json(settings.paper_delay_state_path, delay_state)

    market_size = _file_size(settings.paper_market_tape_path)
    if market_size < int(delay_state.get("market_offset", 0) or 0):
        logger.warning(
            "Market tape shrank below delayed cursor; rebasing market cursor"
        )
        delay_state["market_offset"] = market_size
        delay_state["last_market_processed_at"] = None
        _write_json(settings.paper_delay_state_path, delay_state)

    delayed_signals = _build_delayed_signal_state(settings, delay_state)

    synced = 0
    if first_start:
        synced = _sync_signals(
            account,
            delayed_signals.snapshot(),
            initial_snapshot=True,
            event_store=event_store,
            observed_at=str(delay_state.get("activated_at") or datetime.now(UTC).isoformat()),
        )

    now = datetime.now(UTC)
    _write_json(
        settings.paper_state_path,
        _paper_snapshot(account, settings, delay_state, now=now),
    )

    logger.info(
        "Paper engine ready balance=%.2f symbol=%s mode=%s entry_policy=%s "
        "risk=%.2f%% direction_cap=DISABLED daily_stop=%.2f%% delay=%ss "
        "telegram_offset=%s market_offset=%s candidates=%s positions=%s initial_sync=%s",
        account.balance_usd,
        account.symbol,
        account.strategy_mode,
        account.entry_policy,
        settings.paper_risk_pct * 100.0,
        settings.paper_daily_loss_pct * 100.0,
        settings.paper_simulation_delay_seconds,
        delay_state.get("telegram_offset"),
        delay_state.get("market_offset"),
        len(account.candidates),
        len(account.positions),
        synced,
    )

    if delay_initialized:
        logger.info(
            "15-minute paper delay activated. Existing durable paper state is the "
            "baseline; new Telegram events and XAUUSD quotes will be released "
            "after %s seconds.",
            settings.paper_simulation_delay_seconds,
        )

    last_live_quote_key: tuple[Any, ...] | None = None
    last_metadata_write = datetime.min.replace(tzinfo=UTC)

    while True:
        now = datetime.now(UTC)

        live_quote = await gold_spot.quote()
        live_quote_key = _quote_key(live_quote)
        if live_quote is not None and live_quote_key != last_live_quote_key:
            tape_quote = dict(live_quote)
            tape_quote["captured_at"] = now.isoformat()
            tape_quote["feed"] = "standard-bullion:XAUUSD"
            _append_jsonl(settings.paper_market_tape_path, tape_quote)
            last_live_quote_key = live_quote_key

        cutoff = _delay_cutoff(
            settings.paper_simulation_delay_seconds,
            now=now,
        )
        processed_any = False
        account_changed = False

        while True:
            telegram_offset = int(delay_state.get("telegram_offset", 0) or 0)
            market_offset = int(delay_state.get("market_offset", 0) or 0)

            telegram_next = _peek_jsonl(
                settings.telegram_event_log_path,
                telegram_offset,
            )
            market_next = _peek_jsonl(
                settings.paper_market_tape_path,
                market_offset,
            )

            due: list[tuple[datetime, int, str, dict[str, Any], int]] = []
            if telegram_next is not None:
                event, next_offset = telegram_next
                event_time = _telegram_time(event)
                if event_time <= cutoff:
                    due.append(
                        (event_time, 0, "telegram", event, next_offset)
                    )

            if market_next is not None:
                row, next_offset = market_next
                row_time = _market_time(row)
                if row_time <= cutoff:
                    due.append(
                        (row_time, 1, "market", row, next_offset)
                    )

            if not due:
                break

            _, _, kind, payload, next_offset = min(
                due,
                key=lambda item: (item[0], item[1]),
            )

            if kind == "telegram":
                delay_state["telegram_offset"] = next_offset
                event_type = str(payload.get("event_type") or "")
                event_time = _telegram_time(payload)
                delay_state["last_telegram_processed_at"] = event_time.isoformat()
                processed_any = True

                baseline_offset = int(
                    delay_state.get("baseline_telegram_offset", 0) or 0
                )
                if (
                    next_offset > baseline_offset
                    and event_type == "historical_message"
                ):
                    _append_audit(
                        event_store,
                        kind="paper_delayed_historical_ignored",
                        data={
                            "chat_id": payload.get("chat_id"),
                            "message_id": payload.get("message_id"),
                            "simulation_time": event_time.isoformat(),
                        },
                    )
                    continue

                if event_type == "_malformed":
                    continue

                delayed_signals.ingest_event(payload)
                synced = _sync_signals(
                    account,
                    delayed_signals.snapshot(),
                    initial_snapshot=False,
                    event_store=event_store,
                    observed_at=event_time.isoformat(),
                )
                if synced:
                    account_changed = True
                    logger.info(
                        "Delayed paper signal state advanced simulation_time=%s "
                        "changed=%s candidates=%s",
                        event_time.isoformat(),
                        synced,
                        len(account.candidates),
                    )

            else:
                delay_state["market_offset"] = next_offset
                row_time = _market_time(payload)
                delay_state["last_market_processed_at"] = row_time.isoformat()
                processed_any = True

                if str(payload.get("event_type") or "") == "_malformed":
                    continue

                if account.strategy_mode != "observe_only":
                    account_changed = (
                        strategy.process(
                            account,
                            payload,
                            timestamp=row_time.isoformat(),
                        )
                        or account_changed
                    )
                elif payload.get("price") is not None:
                    price = float(payload["price"])
                    account.mark_quote(
                        bid=float(payload.get("bid") or price),
                        ask=float(payload.get("ask") or price),
                        price=price,
                        marked_at=row_time.isoformat(),
                    )
                    account_changed = True

        if processed_any:
            _write_json(settings.paper_delay_state_path, delay_state)

        if (
            processed_any
            or account_changed
            or (now - last_metadata_write).total_seconds() >= 5
        ):
            _write_json(
                settings.paper_state_path,
                _paper_snapshot(account, settings, delay_state, now=now),
            )
            last_metadata_write = now

        await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
