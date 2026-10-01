from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dkcapital.config import Settings
from dkcapital.paper_engine import _market_time, _telegram_time
from dkcapital.paper_trading import PaperAccount
from dkcapital.signal_state import SignalState
from dkcapital.xau_strategy import XauSignalFollowingStrategy, XauStrategyConfig

CONFIRM_TOKEN = "BACKFILL-LAST-SIGNALS"


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
    backup = path.with_name("{}.pre-last-signal-backfill-{}".format(path.name, stamp))
    shutil.copy2(path, backup)
    return backup


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


def _full_signal_state(events: list[dict[str, Any]]) -> SignalState:
    state = SignalState()
    for event in events:
        state.ingest_event(event, rebuild=False)
    state.rebuild()
    return state


def _selected_signal_ids(
    state: SignalState,
    *,
    count: int,
    before: datetime,
) -> list[str]:
    rows = []
    for raw in state.snapshot().get("signals") or []:
        if str(raw.get("symbol") or "").upper() != "XAUUSD":
            continue
        opened = _parse_dt(raw.get("opened_at"))
        if opened is None or opened >= before:
            continue
        rows.append((opened, str(raw.get("signal_id") or "")))
    rows.sort()
    return [signal_id for _, signal_id in rows[-count:] if signal_id]


def _market_coverage(
    rows: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
    maximum_gap_seconds: float,
) -> tuple[bool, str, list[tuple[datetime, dict[str, Any]]]]:
    timed = []
    for row in rows:
        when = _market_time(row)
        if when < start or when > end:
            continue
        if row.get("price") is None and row.get("bid") is None and row.get("ask") is None:
            continue
        timed.append((when, row))
    timed.sort(key=lambda item: item[0])

    if not timed:
        return False, "No Standard Bullion market tape rows cover the requested window.", []

    if timed[0][0] > start:
        return (
            False,
            "Market tape starts at {} but the oldest selected signal starts at {}.".format(
                timed[0][0].isoformat(),
                start.isoformat(),
            ),
            timed,
        )

    if timed[-1][0] < end:
        return (
            False,
            "Market tape ends at {} but the reset boundary is {}.".format(
                timed[-1][0].isoformat(),
                end.isoformat(),
            ),
            timed,
        )

    previous = timed[0][0]
    for when, _ in timed[1:]:
        gap = (when - previous).total_seconds()
        if gap > maximum_gap_seconds:
            return (
                False,
                "Market tape has a {:.1f}s gap between {} and {}; replay would not be reliable.".format(
                    gap,
                    previous.isoformat(),
                    when.isoformat(),
                ),
                timed,
            )
        previous = when

    return True, "coverage_ok", timed


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


def _replay(
    settings: Settings,
    *,
    telegram_events: list[dict[str, Any]],
    market_rows: list[tuple[datetime, dict[str, Any]]],
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
    for event in telegram_events:
        when = _telegram_time(event)
        if when <= end:
            timeline.append((when, 0, "telegram", event))
    for when, row in market_rows:
        if start <= when <= end:
            timeline.append((when, 1, "market", row))
    timeline.sort(key=lambda item: (item[0], item[1]))

    selected_seen: set[str] = set()

    for when, _, kind, payload in timeline:
        if kind == "telegram":
            state.ingest_event(payload)
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
            continue

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
    return {
        "balance_usd": account.balance_usd,
        "equity_usd": account.equity_usd,
        "realized_pnl_usd": account.realized_pnl_usd,
        "candidate_count": len(candidates),
        "position_count": len(positions),
        "open_positions": sum(1 for row in positions if row.status == "OPEN"),
        "closed_positions": sum(1 for row in positions if row.status == "CLOSED"),
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
            "Replay the last N pre-reset XAUUSD signals into a clean DK Capital "
            "paper account using only preserved Standard Bullion tape."
        )
    )
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--max-market-gap-seconds",
        type=float,
        default=45.0,
        help="Refuse replay if the preserved Standard Bullion tape has a larger gap.",
    )
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    if args.count < 1:
        raise SystemExit("--count must be at least 1")

    settings = Settings.from_env()
    delay_state = _read_json(settings.paper_delay_state_path)
    reset_at = _parse_dt(delay_state.get("activated_at"))
    if reset_at is None:
        raise SystemExit("Paper delay state has no valid activation/reset timestamp.")

    telegram_offset = int(delay_state.get("baseline_telegram_offset", 0) or 0)
    market_offset = int(delay_state.get("market_offset", 0) or 0)
    if telegram_offset <= 0 or market_offset <= 0:
        raise SystemExit("Reset baseline offsets are missing; refusing historical replay.")

    telegram_events = _read_jsonl_until(
        settings.telegram_event_log_path,
        telegram_offset,
    )
    market_rows_raw = _read_jsonl_until(
        settings.paper_market_tape_path,
        market_offset,
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

    final_snapshot = {
        str(row.get("signal_id") or ""): row
        for row in full_state.snapshot().get("signals") or []
    }
    selected_rows = [final_snapshot[signal_id] for signal_id in selected]
    starts = [_parse_dt(row.get("opened_at")) for row in selected_rows]
    starts = [value for value in starts if value is not None]
    if not starts:
        raise SystemExit("Selected signals have no usable timestamps.")
    start = min(starts)

    ok, reason, market_rows = _market_coverage(
        market_rows_raw,
        start=start,
        end=reset_at,
        maximum_gap_seconds=args.max_market_gap_seconds,
    )

    print("Requested signals: {}".format(args.count))
    print("Reset boundary: {}".format(reset_at.isoformat()))
    print("Oldest selected signal: {}".format(start.isoformat()))
    print("Preserved market rows in window: {}".format(len(market_rows)))
    print()
    print("Selected signals:")
    for row in selected_rows:
        print(
            "  {} | {} | {} | opened={} | final_status={}".format(
                row.get("signal_id"),
                row.get("direction"),
                row.get("source_style"),
                row.get("opened_at"),
                row.get("status"),
            )
        )

    if not ok:
        raise SystemExit(
            "\nSAFE REFUSAL: {}\n"
            "The last {} signals cannot be accurately backfilled from the preserved "
            "Standard Bullion tape. No paper state was changed.".format(
                reason,
                args.count,
            )
        )

    account = _replay(
        settings,
        telegram_events=telegram_events,
        market_rows=market_rows,
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
            "Dry run only. If the summary is correct, rerun with "
            "--confirm {}.".format(CONFIRM_TOKEN)
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
    simulation = current.get("simulation") if isinstance(current.get("simulation"), dict) else None
    payload = account.snapshot()
    if simulation is not None:
        payload["simulation"] = simulation
    _write_json(settings.paper_state_path, payload)

    settings.paper_event_log_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.paper_event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event_type": "paper_last_signals_backfilled",
                    "observed_at": datetime.now(UTC).isoformat(),
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
    print("Backfill written successfully.")
    if backups:
        print("Backups:")
        for path in backups:
            print("  {}".format(path))


if __name__ == "__main__":
    run()
