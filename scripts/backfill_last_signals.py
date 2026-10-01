from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dkcapital.config import Settings
from dkcapital.paper_trading import PaperAccount
from dkcapital.signal_state import SignalState
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

CONFIRM_TOKEN = "BACKFILL-XAUUSD"
TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
TWELVE_DATA_SYMBOL = "XAU/USD"
TWELVE_DATA_INTERVAL = "1min"
CHUNK_HOURS = 24


@dataclass(frozen=True)
class Candle:
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


def _event_time(event: dict[str, Any]) -> datetime:
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


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _read_jsonl_until(path: Path, end_offset: int) -> list[dict[str, Any]]:
    if not path.exists() or end_offset <= 0:
        return []
    rows: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        while handle.tell() < end_offset:
            raw = handle.readline()
            if not raw or not raw.endswith(b"\n"):
                break
            if handle.tell() > end_offset:
                break
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)


def _backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name("{}.pre-xauusd-backfill-{}".format(path.name, stamp))
    shutil.copy2(path, backup)
    return backup


def _request_json(url: str, api_key: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "DKCapital-XAUUSD-Backfill/1.0",
        },
    )
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Twelve Data returned a non-object response.")
    if str(payload.get("status") or "").lower() == "error":
        raise RuntimeError(
            "Twelve Data error {}: {}".format(
                payload.get("code"),
                payload.get("message") or "unknown error",
            )
        )
    return payload


def _fetch_xauusd_candles(
    *,
    api_key: str,
    start: datetime,
    end: datetime,
    base_url: str,
) -> list[Candle]:
    cursor = start.astimezone(UTC).replace(second=0, microsecond=0)
    final = end.astimezone(UTC) + timedelta(minutes=1)
    rows: dict[datetime, Candle] = {}

    while cursor < final:
        chunk_end = min(final, cursor + timedelta(hours=CHUNK_HOURS))
        query = urlencode(
            {
                "symbol": TWELVE_DATA_SYMBOL,
                "interval": TWELVE_DATA_INTERVAL,
                "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC",
                "order": "ASC",
                "apikey": api_key,
            }
        )
        payload = _request_json(
            "{}/time_series?{}".format(base_url.rstrip("/"), query),
            api_key,
        )

        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        returned_symbol = str(meta.get("symbol") or "")
        if returned_symbol and returned_symbol.upper().replace(" ", "") != TWELVE_DATA_SYMBOL:
            raise RuntimeError(
                "Refusing non-XAU/USD market data. Twelve Data returned symbol {!r}.".format(
                    returned_symbol
                )
            )

        values = payload.get("values") or []
        if not isinstance(values, list):
            raise RuntimeError("Twelve Data response is missing time-series values.")

        for raw in values:
            if not isinstance(raw, dict):
                continue
            when = _parse_dt(raw.get("datetime"))
            if when is None:
                continue
            try:
                candle = Candle(
                    start=when,
                    open=float(raw["open"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    close=float(raw["close"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            rows[when] = candle

        cursor = chunk_end
        time.sleep(0.15)

    candles = [rows[key] for key in sorted(rows)]
    if not candles:
        raise RuntimeError("Twelve Data returned no XAU/USD 1-minute candles.")
    return candles


def _full_signal_state(events: list[dict[str, Any]]) -> SignalState:
    state = SignalState()
    for event in sorted(events, key=_event_time):
        state.ingest_event(event, rebuild=False)
    state.rebuild()
    return state


def _selected_signal_ids(
    state: SignalState,
    *,
    count: int,
    before: datetime,
) -> list[str]:
    rows: list[tuple[datetime, str]] = []
    for raw in state.snapshot().get("signals") or []:
        if str(raw.get("symbol") or "").upper() != "XAUUSD":
            continue
        opened = _parse_dt(raw.get("opened_at"))
        if opened is None or opened >= before:
            continue
        signal_id = str(raw.get("signal_id") or "")
        if signal_id:
            rows.append((opened, signal_id))
    rows.sort()
    return [signal_id for _, signal_id in rows[-count:]]


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


def _filtered_snapshot(state: SignalState, selected_ids: set[str]) -> dict[str, Any]:
    snapshot = state.snapshot()
    return {
        "signals": [
            row
            for row in snapshot.get("signals") or []
            if str(row.get("signal_id") or "") in selected_ids
        ]
    }


def _sync_selected(
    account: PaperAccount,
    state: SignalState,
    selected_ids: set[str],
    *,
    observed_at: str,
) -> None:
    for signal in _filtered_snapshot(state, selected_ids).get("signals") or []:
        account.sync_signal(
            signal,
            initial_snapshot=False,
            observed_at=observed_at,
        )


def _quote(price: float, timestamp: datetime) -> dict[str, Any]:
    return {
        "symbol": "XAUUSD",
        "price": float(price),
        "bid": float(price),
        "ask": float(price),
        "computed_at": timestamp.isoformat(),
        "source": "twelvedata:XAU/USD:1min",
    }


def _candle_points(candle: Candle) -> list[tuple[datetime, float]]:
    # Twelve Data supplies genuine XAU/USD OHLC at 1-minute resolution, but not
    # the exact intraminute tick order. Use a deterministic OHLC path so the
    # replay is reproducible. No proxy instrument is ever used.
    if candle.close >= candle.open:
        values = [candle.open, candle.low, candle.high, candle.close]
    else:
        values = [candle.open, candle.high, candle.low, candle.close]

    seconds = [0, 20, 40, 59]
    points: list[tuple[datetime, float]] = []
    for index, value in enumerate(values):
        if points and abs(points[-1][1] - value) <= 1e-12:
            continue
        points.append((candle.start + timedelta(seconds=seconds[index]), value))
    return points


def _clean_event(event: dict[str, Any]) -> dict[str, Any]:
    # Historical replay must be driven exclusively by the XAU/USD API. Remove
    # any legacy market snapshot attached to the Telegram event.
    cleaned = dict(event)
    cleaned.pop("market", None)
    return cleaned


def _replay(
    settings: Settings,
    *,
    telegram_events: list[dict[str, Any]],
    candles: list[Candle],
    selected_ids: set[str],
    start: datetime,
    end: datetime,
) -> PaperAccount:
    state = SignalState()
    account = PaperAccount(
        starting_balance_usd=settings.paper_starting_balance_usd,
        symbol=settings.paper_symbol,
        strategy_mode=settings.paper_strategy_mode,
        entry_policy=settings.paper_entry_policy,
    )
    account.activate_strategy(
        settings.paper_strategy_mode,
        activated_at=start.isoformat(),
    )
    strategy = _strategy(settings)

    timeline: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for raw_event in telegram_events:
        event = _clean_event(raw_event)
        when = _event_time(event)
        if when <= end:
            timeline.append((when, 0, "telegram", event))

    for candle in candles:
        for when, price in _candle_points(candle):
            if start <= when <= end:
                timeline.append((when, 1, "market", _quote(price, when)))

    timeline.sort(key=lambda item: (item[0], item[1]))

    selected_seen: set[str] = set()
    for when, _, kind, payload in timeline:
        if kind == "telegram":
            state.ingest_event(payload)
            snapshot = _filtered_snapshot(state, selected_ids)
            selected_seen |= {
                str(row.get("signal_id") or "")
                for row in snapshot.get("signals") or []
            }
            _sync_selected(
                account,
                state,
                selected_ids,
                observed_at=when.isoformat(),
            )
        else:
            strategy.process(
                account,
                payload,
                timestamp=when.isoformat(),
            )

    missing = selected_ids - selected_seen
    if missing:
        raise RuntimeError(
            "Selected signals were not reconstructed during replay: {}".format(
                ", ".join(sorted(missing))
            )
        )
    return account


def _summary(account: PaperAccount, selected_ids: set[str]) -> dict[str, Any]:
    candidates = [
        row for row in account.candidates.values() if row.signal_id in selected_ids
    ]
    positions = [
        row for row in account.positions.values() if row.signal_id in selected_ids
    ]
    closed = [row for row in positions if row.status == "CLOSED"]
    return {
        "source": "Twelve Data XAU/USD 1-minute OHLC",
        "starting_balance_usd": account.starting_balance_usd,
        "balance_usd": account.balance_usd,
        "equity_usd": account.equity_usd,
        "realized_pnl_usd": account.realized_pnl_usd,
        "candidate_count": len(candidates),
        "position_count": len(positions),
        "open_positions": sum(1 for row in positions if row.status == "OPEN"),
        "closed_positions": len(closed),
        "signals": [
            {
                "signal_id": row.signal_id,
                "direction": row.direction,
                "status": row.execution_status,
                "note": row.execution_note,
            }
            for row in candidates
        ],
    }


def run() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill the latest DK Capital XAUUSD signals using Twelve Data "
            "XAU/USD 1-minute historical market data. No XAUUSDT proxy is used."
        )
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--base-url",
        default=TWELVE_DATA_BASE_URL,
        help="Twelve Data API base URL.",
    )
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    api_key = os.getenv("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "TWELVE_DATA_API_KEY is required. Add it to /opt/dkcapital/.env "
            "before running the XAU/USD backfill."
        )

    settings = Settings.from_env()
    delay_state = _read_json(settings.paper_delay_state_path)
    reset_at = _parse_dt(delay_state.get("activated_at"))
    if reset_at is None:
        raise SystemExit("Paper delay state has no valid reset timestamp.")

    telegram_offset = int(delay_state.get("baseline_telegram_offset", 0) or 0)
    if telegram_offset <= 0:
        raise SystemExit("Telegram reset baseline is missing; refusing backfill.")

    telegram_events = _read_jsonl_until(
        settings.telegram_event_log_path,
        telegram_offset,
    )
    full_state = _full_signal_state(telegram_events)
    selected = _selected_signal_ids(
        full_state,
        count=args.count,
        before=reset_at,
    )
    if len(selected) < args.count:
        raise SystemExit(
            "Only {} pre-reset XAUUSD signals were found; requested {}.".format(
                len(selected),
                args.count,
            )
        )

    final_rows = {
        str(row.get("signal_id") or ""): row
        for row in full_state.snapshot().get("signals") or []
    }
    selected_rows = [final_rows[signal_id] for signal_id in selected]
    starts = [_parse_dt(row.get("opened_at")) for row in selected_rows]
    starts = [value for value in starts if value is not None]
    if not starts:
        raise SystemExit("Selected signals have no usable timestamps.")
    start = min(starts)

    print("Market source: Twelve Data XAU/USD")
    print("Resolution: 1-minute OHLC")
    print("Proxy instruments: DISABLED")
    print("Directional risk cap: DISABLED")
    print("Requested signals: {}".format(args.count))
    print("Replay start: {}".format(start.isoformat()))
    print("Replay end: {}".format(reset_at.isoformat()))
    print()
    print("Selected signals:")
    for row in selected_rows:
        print(
            "  {} | {} | {} | opened={} | provider_status={}".format(
                row.get("signal_id"),
                row.get("direction"),
                row.get("source_style"),
                row.get("opened_at"),
                row.get("status"),
            )
        )

    print()
    print("Fetching genuine XAU/USD historical candles...")
    candles = _fetch_xauusd_candles(
        api_key=api_key,
        start=start - timedelta(minutes=1),
        end=reset_at,
        base_url=args.base_url,
    )
    print(
        "Fetched {} XAU/USD candles from {} through {}.".format(
            len(candles),
            candles[0].start.isoformat(),
            candles[-1].start.isoformat(),
        )
    )

    account = _replay(
        settings,
        telegram_events=telegram_events,
        candles=candles,
        selected_ids=set(selected),
        start=start,
        end=reset_at,
    )
    summary = _summary(account, set(selected))

    print()
    print("Replay summary:")
    print(json.dumps(summary, indent=2))

    if args.confirm != CONFIRM_TOKEN:
        print()
        print(
            "Dry run only. No paper state changed. If the replay summary is "
            "correct, rerun with --confirm {}.".format(CONFIRM_TOKEN)
        )
        return

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backups = [
        backup
        for path in (
            settings.paper_state_path,
            settings.paper_event_log_path,
        )
        if (backup := _backup(path, stamp)) is not None
    ]

    current = _read_json(settings.paper_state_path)
    simulation = (
        current.get("simulation")
        if isinstance(current.get("simulation"), dict)
        else None
    )
    payload = account.snapshot()
    if simulation is not None:
        payload["simulation"] = simulation
    _write_json(settings.paper_state_path, payload)

    settings.paper_event_log_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.paper_event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_type": "paper_xauusd_history_backfilled",
                    "observed_at": datetime.now(UTC).isoformat(),
                    "source": "twelvedata:XAU/USD:1min",
                    "count": args.count,
                    "selected_signal_ids": selected,
                    "replay_start": start.isoformat(),
                    "replay_end": reset_at.isoformat(),
                    "summary": summary,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )

    print()
    print("XAU/USD backfill written successfully.")
    if backups:
        print("Backups:")
        for path in backups:
            print("  {}".format(path))


if __name__ == "__main__":
    run()
