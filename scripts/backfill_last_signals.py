from __future__ import annotations

import argparse
import json
import lzma
import shutil
import struct
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dkcapital.config import Settings
from dkcapital.paper_trading import PaperAccount
from dkcapital.signal_state import SignalState
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

CONFIRM_TOKEN = "BACKFILL-XAUUSD"
DUKASCOPY_BASE_URLS = (
    "https://www.dukascopy.com/datafeed",
    "https://datafeed.dukascopy.com/datafeed",
)
DUKASCOPY_SYMBOL = "XAUUSD"
DUKASCOPY_PRICE_SCALE = 1000.0
TICK_RECORD = struct.Struct(">IIIff")


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


def _clean_event(event: dict[str, Any]) -> dict[str, Any]:
    # Ignore every legacy embedded market snapshot. Historical replay is driven
    # exclusively by Dukascopy XAUUSD bid/ask ticks.
    cleaned = dict(event)
    cleaned.pop("market", None)
    return cleaned


def _hour_url(base_url: str, hour: datetime) -> str:
    hour = hour.astimezone(UTC)
    zero_based_month = hour.month - 1
    return (
        f"{base_url.rstrip('/')}/{DUKASCOPY_SYMBOL}/"
        f"{hour.year:04d}/{zero_based_month:02d}/{hour.day:02d}/"
        f"{hour.hour:02d}h_ticks.bi5"
    )


def _download_hour(hour: datetime, timeout: int = 30) -> bytes | None:
    last_error: Exception | None = None
    for base_url in DUKASCOPY_BASE_URLS:
        url = _hour_url(base_url, hour)
        request = Request(
            url,
            headers={
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 DKCapital-XAUUSD-Backfill/1.0",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read()
            if not payload:
                return None
            return payload
        except HTTPError as exc:
            if exc.code == 404:
                return None
            last_error = exc
        except (URLError, TimeoutError, OSError) as exc:
            last_error = exc

    if last_error is not None:
        raise RuntimeError(
            "Unable to download Dukascopy XAUUSD ticks for {}: {}".format(
                hour.isoformat(),
                last_error,
            )
        ) from last_error
    return None


def _decode_hour(hour: datetime, compressed: bytes | None) -> list[dict[str, Any]]:
    if not compressed:
        return []

    try:
        raw = lzma.decompress(compressed)
    except lzma.LZMAError as exc:
        raise RuntimeError(
            "Dukascopy returned an invalid XAUUSD BI5 payload for {}.".format(
                hour.isoformat()
            )
        ) from exc

    if len(raw) % TICK_RECORD.size != 0:
        raise RuntimeError(
            "Dukascopy XAUUSD tick payload length {} is not divisible by {}.".format(
                len(raw),
                TICK_RECORD.size,
            )
        )

    ticks: list[dict[str, Any]] = []
    for offset in range(0, len(raw), TICK_RECORD.size):
        ms, ask_raw, bid_raw, ask_volume, bid_volume = TICK_RECORD.unpack_from(
            raw,
            offset,
        )
        timestamp = hour + timedelta(milliseconds=int(ms))
        ask = float(ask_raw) / DUKASCOPY_PRICE_SCALE
        bid = float(bid_raw) / DUKASCOPY_PRICE_SCALE

        # Fail closed if the decoder scale or source is wrong.
        if not (100.0 <= bid <= 10000.0 and 100.0 <= ask <= 10000.0):
            raise RuntimeError(
                "Decoded Dukascopy XAUUSD price is outside sanity bounds: "
                "bid={} ask={} at {}.".format(
                    bid,
                    ask,
                    timestamp.isoformat(),
                )
            )
        if ask + 1e-9 < bid:
            raise RuntimeError(
                "Decoded Dukascopy XAUUSD spread is inverted at {}: bid={} ask={}.".format(
                    timestamp.isoformat(),
                    bid,
                    ask,
                )
            )

        ticks.append(
            {
                "symbol": "XAUUSD",
                "price": (bid + ask) / 2.0,
                "bid": bid,
                "ask": ask,
                "computed_at": timestamp.isoformat(),
                "source": "dukascopy:XAUUSD:tick",
                "ask_volume": float(ask_volume),
                "bid_volume": float(bid_volume),
            }
        )
    return ticks


def _iter_hours(start: datetime, end: datetime):
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    final = end.astimezone(UTC)
    while cursor <= final:
        yield cursor
        cursor += timedelta(hours=1)


def _replay(
    settings: Settings,
    *,
    telegram_events: list[dict[str, Any]],
    selected_ids: set[str],
    start: datetime,
    end: datetime,
) -> tuple[PaperAccount, int, int]:
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

    events = [
        (_event_time(raw), _clean_event(raw))
        for raw in telegram_events
        if _event_time(raw) <= end
    ]
    events.sort(key=lambda item: item[0])
    event_index = 0

    # Build the parser context up to the oldest selected signal.
    while event_index < len(events) and events[event_index][0] < start:
        when, event = events[event_index]
        state.ingest_event(event)
        event_index += 1

    selected_seen: set[str] = set()
    downloaded_hours = 0
    total_ticks = 0

    for hour in _iter_hours(start, end):
        payload = _download_hour(hour)
        ticks = _decode_hour(hour, payload)
        if ticks:
            downloaded_hours += 1
            total_ticks += len(ticks)

        hour_end = min(hour + timedelta(hours=1), end + timedelta(microseconds=1))
        timeline: list[tuple[datetime, int, str, dict[str, Any]]] = []

        while event_index < len(events) and events[event_index][0] < hour_end:
            when, event = events[event_index]
            if when >= start:
                timeline.append((when, 0, "telegram", event))
            else:
                state.ingest_event(event)
            event_index += 1

        for tick in ticks:
            when = _parse_dt(tick.get("computed_at"))
            if when is None or when < start or when > end:
                continue
            timeline.append((when, 1, "market", tick))

        timeline.sort(key=lambda item: (item[0], item[1]))

        for when, _, kind, item in timeline:
            if kind == "telegram":
                state.ingest_event(item)
                snapshot = _filtered_snapshot(state, selected_ids)
                current_ids = {
                    str(row.get("signal_id") or "")
                    for row in snapshot.get("signals") or []
                }
                selected_seen |= current_ids
                _sync_selected(
                    account,
                    state,
                    selected_ids,
                    observed_at=when.isoformat(),
                )
            else:
                strategy.process(
                    account,
                    item,
                    timestamp=when.isoformat(),
                )

        print(
            "{} | ticks={} | candidates={} | positions={} | balance={:.2f}".format(
                hour.isoformat(),
                len(ticks),
                len(account.candidates),
                len(account.positions),
                account.balance_usd,
            )
        )
        time.sleep(0.05)

    # Apply any Telegram events exactly at the replay endpoint.
    while event_index < len(events) and events[event_index][0] <= end:
        when, event = events[event_index]
        state.ingest_event(event)
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
        event_index += 1

    missing = selected_ids - selected_seen
    if missing:
        raise RuntimeError(
            "Selected signals were not reconstructed during replay: {}".format(
                ", ".join(sorted(missing))
            )
        )

    if total_ticks <= 0:
        raise RuntimeError(
            "Dukascopy returned no XAUUSD ticks for the replay window."
        )

    return account, downloaded_hours, total_ticks


def _summary(account: PaperAccount, selected_ids: set[str]) -> dict[str, Any]:
    candidates = [
        row for row in account.candidates.values() if row.signal_id in selected_ids
    ]
    positions = [
        row for row in account.positions.values() if row.signal_id in selected_ids
    ]
    closed = [row for row in positions if row.status == "CLOSED"]
    return {
        "source": "Dukascopy XAUUSD tick bid/ask",
        "starting_balance_usd": account.starting_balance_usd,
        "balance_usd": account.balance_usd,
        "equity_usd": account.equity_usd,
        "realized_pnl_usd": account.realized_pnl_usd,
        "unrealized_pnl_usd": account.unrealized_pnl_usd,
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
            "Backfill the latest DK Capital XAUUSD signals from free Dukascopy "
            "XAUUSD bid/ask tick history. No API key and no XAUUSDT proxy."
        )
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1")

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

    print("Market source: Dukascopy XAUUSD")
    print("Resolution: raw bid/ask ticks")
    print("Cost: free, no API key")
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
    print("Downloading free Dukascopy XAUUSD tick history...")
    account, downloaded_hours, total_ticks = _replay(
        settings,
        telegram_events=telegram_events,
        selected_ids=set(selected),
        start=start,
        end=reset_at,
    )
    summary = _summary(account, set(selected))

    print()
    print(
        "Downloaded XAUUSD history: {} populated hours, {:,} ticks.".format(
            downloaded_hours,
            total_ticks,
        )
    )
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
                    "source": "dukascopy:XAUUSD:tick",
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
    print("XAUUSD tick backfill written successfully.")
    if backups:
        print("Backups:")
        for path in backups:
            print("  {}".format(path))


if __name__ == "__main__":
    run()
