from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from telethon import TelegramClient, utils
from telethon.sessions import StringSession

from dkcapital.config import ChatRef, Settings
from dkcapital.paper_trading import PaperAccount
from dkcapital.signal_state import SignalState
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

BYBIT_PUBLIC_BASE_URL = "https://api.bybit.com"
CONFIRM_TOKEN = "REBUILD-DK-PAPER"
MESSAGE_EVENT_TYPES = {"new_message", "historical_message", "message_edited"}


@dataclass(frozen=True)
class Kline:
    start: datetime
    open: float
    high: float
    low: float
    close: float


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


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _ceil_minute(value: datetime) -> datetime:
    value = value.astimezone(UTC)
    base = value.replace(second=0, microsecond=0)
    if value == base:
        return base
    return base + timedelta(minutes=1)


def _reply_to_message_id(message: Any) -> int | None:
    reply_to = getattr(message, "reply_to", None)
    return getattr(reply_to, "reply_to_msg_id", None) if reply_to else None


def _payload(message: Any, *, source_name: str, source_chat_id: int) -> dict[str, Any]:
    media = getattr(message, "media", None)
    return {
        "event_type": "historical_message",
        "observed_at": datetime.now(UTC).isoformat(),
        "source_name": source_name,
        "chat_id": source_chat_id,
        "message_id": getattr(message, "id", None),
        "sender_id": getattr(message, "sender_id", None),
        "date": _iso(getattr(message, "date", None)),
        "edit_date": _iso(getattr(message, "edit_date", None)),
        "reply_to_message_id": _reply_to_message_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "text": getattr(message, "raw_text", "") or "",
        "has_media": media is not None,
        "media_type": type(media).__name__ if media is not None else None,
    }


async def _resolve_entity(client: TelegramClient, chat: ChatRef):
    dialogs = await client.get_dialogs()
    dialog_entities = {
        utils.get_peer_id(dialog.entity): dialog.entity
        for dialog in dialogs
    }
    if isinstance(chat, int) and chat in dialog_entities:
        return dialog_entities[chat]
    return await client.get_entity(chat)


async def _capture_history(
    settings: Settings,
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    client = TelegramClient(
        StringSession(settings.telegram_session_string),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("The Telegram session is not authorized.")

    events: list[dict[str, Any]] = []
    try:
        for configured_chat in settings.telegram_source_chats:
            entity = await _resolve_entity(client, configured_chat)
            source_chat_id = utils.get_peer_id(entity)
            source_name = (
                getattr(entity, "title", None)
                or getattr(entity, "username", None)
                or str(source_chat_id)
            )
            count = 0
            async for message in client.iter_messages(entity):
                message_date = getattr(message, "date", None)
                if message_date is None:
                    continue
                if message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)
                message_date = message_date.astimezone(UTC)
                if message_date < since:
                    break
                events.append(
                    _payload(
                        message,
                        source_name=source_name,
                        source_chat_id=source_chat_id,
                    )
                )
                count += 1
            print(f"Captured {count} historical messages from {source_name}.")
    finally:
        await client.disconnect()

    events.sort(key=_event_sort_key)
    return events


def _event_effective_dt(event: dict[str, Any]) -> datetime:
    event_type = str(event.get("event_type") or "")
    values: list[Any]
    if event_type == "message_edited":
        values = [event.get("edit_date"), event.get("observed_at"), event.get("date")]
    elif event_type == "message_deleted":
        values = [event.get("observed_at"), event.get("date")]
    else:
        values = [event.get("date"), event.get("observed_at")]
    for value in values:
        parsed = _parse_dt(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=UTC)


def _event_sort_key(event: dict[str, Any]) -> tuple[datetime, int, int, str]:
    message_id = event.get("message_id")
    if message_id is None:
        ids = event.get("message_ids") or [0]
        message_id = ids[0] if ids else 0
    return (
        _event_effective_dt(event),
        int(event.get("chat_id") or 0),
        int(message_id or 0),
        str(event.get("event_type") or ""),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                print(f"Skipping malformed JSONL line {line_number} in {path}.")
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _merge_events(
    historical: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    represented: set[tuple[int, int]] = set()
    latest_existing: dict[tuple[int, int], tuple[datetime, str]] = {}

    for event in existing:
        chat_id = event.get("chat_id")
        if chat_id is None:
            continue
        event_type = str(event.get("event_type") or "")
        if event_type in MESSAGE_EVENT_TYPES:
            message_id = event.get("message_id")
            if message_id is None:
                continue
            key = (int(chat_id), int(message_id))
            represented.add(key)
            current = latest_existing.get(key)
            effective = _event_effective_dt(event)
            if current is None or effective >= current[0]:
                latest_existing[key] = (effective, str(event.get("text") or ""))
        elif event_type == "message_deleted":
            for message_id in event.get("message_ids") or []:
                represented.add((int(chat_id), int(message_id)))

    additions: list[dict[str, Any]] = []
    for event in historical:
        chat_id = event.get("chat_id")
        message_id = event.get("message_id")
        if chat_id is None or message_id is None:
            continue
        key = (int(chat_id), int(message_id))
        if key not in represented:
            additions.append(event)
            represented.add(key)
            continue

        latest = latest_existing.get(key)
        edit_dt = _parse_dt(event.get("edit_date"))
        if latest is None or edit_dt is None:
            continue
        if edit_dt > latest[0] and str(event.get("text") or "") != latest[1]:
            recovered_edit = dict(event)
            recovered_edit["event_type"] = "message_edited"
            recovered_edit["observed_at"] = datetime.now(UTC).isoformat()
            additions.append(recovered_edit)

    merged = [*existing, *additions]
    merged.sort(key=_event_sort_key)
    return merged


def _request_json(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "DKCapital-PaperBackfill/1.0",
        },
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_klines(
    *,
    start: datetime,
    end: datetime,
    symbol: str,
    base_url: str,
) -> list[Kline]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    minute_ms = 60_000
    block_minutes = 999
    cursor = start_ms
    rows_by_start: dict[int, Kline] = {}

    while cursor <= end_ms:
        block_end = min(end_ms, cursor + block_minutes * minute_ms)
        query = urlencode(
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "start": cursor,
                "end": block_end,
                "limit": 1000,
            }
        )
        payload = _request_json(
            f"{base_url.rstrip('/')}/v5/market/kline?{query}"
        )
        if int(payload.get("retCode", -1)) != 0:
            raise RuntimeError(
                f"Bybit kline error {payload.get('retCode')}: "
                f"{payload.get('retMsg', 'unknown error')}"
            )

        for raw in payload.get("result", {}).get("list") or []:
            if len(raw) < 5:
                continue
            ts = int(raw[0])
            rows_by_start[ts] = Kline(
                start=datetime.fromtimestamp(ts / 1000.0, tz=UTC),
                open=float(raw[1]),
                high=float(raw[2]),
                low=float(raw[3]),
                close=float(raw[4]),
            )

        cursor = block_end + minute_ms
        time.sleep(0.03)

    return [rows_by_start[key] for key in sorted(rows_by_start)]


def _quote(price: float, timestamp: datetime) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD",
        "venue_symbol": "XAUUSDT",
        "price": float(price),
        "bid": float(price),
        "ask": float(price),
        "computed_at": timestamp.isoformat(),
        "source": "bybit:XAUUSDT:historical_1m",
        "is_stale": False,
    }


def _candle_path(candle: Kline) -> list[float]:
    if candle.close >= candle.open:
        values = [candle.open, candle.low, candle.high, candle.close]
    else:
        values = [candle.open, candle.high, candle.low, candle.close]

    result: list[float] = []
    for value in values:
        if not result or abs(result[-1] - value) > 1e-12:
            result.append(value)
    return result


def _open_positions(account: PaperAccount) -> bool:
    return any(position.status == "OPEN" for position in account.positions.values())


def _sync_account(
    account: PaperAccount,
    state: SignalState,
    *,
    observed_at: str,
) -> int:
    changed = 0
    for signal in state.snapshot().get("signals") or []:
        did_change, _ = account.sync_signal(
            signal,
            initial_snapshot=False,
            observed_at=observed_at,
        )
        changed += int(did_change)
    return changed


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


def _replay_candle(
    strategy: XauSignalFollowingStrategy,
    account: PaperAccount,
    candle: Kline,
) -> None:
    for index, price in enumerate(_candle_path(candle)):
        timestamp = candle.start + timedelta(seconds=min(index * 15, 45))
        strategy.process(
            account,
            _quote(price, timestamp),
            timestamp=timestamp.isoformat(),
        )


def _rebuild(
    settings: Settings,
    *,
    events: list[dict[str, Any]],
    candles: list[Kline],
    since: datetime,
    until: datetime,
) -> tuple[SignalState, PaperAccount]:
    state = SignalState()
    account = PaperAccount(
        starting_balance_usd=settings.paper_starting_balance_usd,
        symbol=settings.paper_symbol,
        strategy_mode=settings.paper_strategy_mode,
        entry_policy=settings.paper_entry_policy,
    )
    account.activate_strategy(
        settings.paper_strategy_mode,
        activated_at=since.isoformat(),
    )
    strategy = _strategy(settings)

    replay_events = [
        event
        for event in events
        if since <= _event_effective_dt(event) <= until
    ]
    replay_events.sort(key=_event_sort_key)

    candle_index = 0
    for event in replay_events:
        event_time = _event_effective_dt(event)
        execution_time = _ceil_minute(event_time)

        while candle_index < len(candles) and candles[candle_index].start < execution_time:
            if _open_positions(account):
                _replay_candle(strategy, account, candles[candle_index])
            candle_index += 1

        state.ingest_event(event)
        _sync_account(
            account,
            state,
            observed_at=event_time.isoformat(),
        )

    while candle_index < len(candles):
        if _open_positions(account):
            _replay_candle(strategy, account, candles[candle_index])
        candle_index += 1

    return state, account


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
    temp.replace(path)


def _backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.pre-rebuild-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _summary(account: PaperAccount) -> dict[str, Any]:
    closed = [position for position in account.positions.values() if position.status == "CLOSED"]
    open_positions = [
        position for position in account.positions.values() if position.status == "OPEN"
    ]
    wins = sum(1 for position in closed if position.realized_pnl_usd > 1e-9)
    losses = sum(1 for position in closed if position.realized_pnl_usd < -1e-9)
    breakeven = len(closed) - wins - losses
    return {
        "starting_balance_usd": account.starting_balance_usd,
        "balance_usd": account.balance_usd,
        "equity_usd": account.equity_usd,
        "realized_pnl_usd": account.realized_pnl_usd,
        "candidate_count": len(account.candidates),
        "position_count": len(account.positions),
        "closed_positions": len(closed),
        "open_positions": len(open_positions),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
    }


async def run() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild DK Capital XAUUSD paper trading from Telegram history and "
            "Bybit XAUUSDT 1-minute market data."
        )
    )
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--archive",
        default="/app/data/telegram-backfill.jsonl",
        help="Where to save the fetched Telegram history.",
    )
    parser.add_argument(
        "--bybit-base-url",
        default=BYBIT_PUBLIC_BASE_URL,
        help="Public Bybit REST base URL.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Write rebuilt state only when this equals {CONFIRM_TOKEN}.",
    )
    args = parser.parse_args()

    if args.days < 1:
        raise SystemExit("--days must be at least 1")

    settings = Settings.from_env()
    settings.validate_telegram_listener()

    until = datetime.now(UTC)
    since = until - timedelta(days=args.days)

    print(
        f"Rebuilding DK Capital paper history from {since.isoformat()} "
        f"through {until.isoformat()}."
    )
    historical = await _capture_history(settings, since=since)
    existing = _read_jsonl(settings.telegram_event_log_path)
    merged = _merge_events(historical, existing)
    replay_events = [
        event
        for event in merged
        if since <= _event_effective_dt(event) <= until
    ]

    print(
        f"Telegram events: historical={len(historical)} existing={len(existing)} "
        f"merged={len(merged)} replay_window={len(replay_events)}"
    )
    print("Fetching Bybit XAUUSDT 1-minute history...")
    candles = await asyncio.to_thread(
        _fetch_klines,
        start=since,
        end=until,
        symbol=settings.bybit_demo_symbol,
        base_url=args.bybit_base_url,
    )
    if not candles:
        raise RuntimeError("Bybit returned no XAUUSDT historical candles.")
    print(
        f"Fetched {len(candles)} candles from {candles[0].start.isoformat()} "
        f"to {candles[-1].start.isoformat()}."
    )

    state, account = await asyncio.to_thread(
        _rebuild,
        settings,
        events=merged,
        candles=candles,
        since=since,
        until=until,
    )
    summary = _summary(account)

    print("Rebuild result:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if args.confirm != CONFIRM_TOKEN:
        print()
        print(
            f"Dry run only. Re-run with --confirm {CONFIRM_TOKEN} "
            "while listener/processor/paper/Discord services are stopped to write state."
        )
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive_path = Path(args.archive)
    paths = [
        settings.telegram_event_log_path,
        settings.signal_state_path,
        settings.paper_state_path,
        settings.paper_event_log_path,
        archive_path,
    ]
    backups = [backup for path in paths if (backup := _backup(path, stamp)) is not None]

    _write_jsonl(archive_path, historical)
    _write_jsonl(settings.telegram_event_log_path, merged)

    signal_payload = state.snapshot()
    signal_payload["updated_at"] = until.isoformat()
    _write_json(settings.signal_state_path, signal_payload)
    _write_json(settings.paper_state_path, account.snapshot())

    with settings.paper_event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_type": "paper_history_rebuilt",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "since": since.isoformat(),
                    "until": until.isoformat(),
                    "telegram_historical_messages": len(historical),
                    "merged_events": len(merged),
                    "candles": len(candles),
                    "summary": summary,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    report_path = settings.paper_state_path.with_name("paper-backfill-report.json")
    _write_json(
        report_path,
        {
            "rebuilt_at": datetime.now(UTC).isoformat(),
            "since": since.isoformat(),
            "until": until.isoformat(),
            "summary": summary,
            "telegram_historical_messages": len(historical),
            "existing_events": len(existing),
            "merged_events": len(merged),
            "replay_events": len(replay_events),
            "candles": len(candles),
            "backups": [str(path) for path in backups],
        },
    )

    print()
    print("Rebuilt paper state written successfully.")
    print(f"Backfill report: {report_path}")
    for backup in backups:
        print(f"Backup: {backup}")


if __name__ == "__main__":
    asyncio.run(run())
