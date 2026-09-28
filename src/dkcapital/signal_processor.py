from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dkcapital.config import Settings
from dkcapital.logging_setup import configure_logging
from dkcapital.signal_state import SignalState

logger = logging.getLogger("dkcapital.signal_processor")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Skipping invalid JSONL line %s in %s", line_number, path)
    return events


def _write_snapshot(path: Path, state: SignalState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = state.snapshot()
    payload["updated_at"] = datetime.now(UTC).isoformat()

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


async def run() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)

    state = SignalState()
    initial_events = _read_jsonl(settings.telegram_event_log_path)
    for event in initial_events:
        state.ingest_event(event, rebuild=False)
    state.rebuild()
    _write_snapshot(settings.signal_state_path, state)

    logger.info(
        "Signal processor loaded events=%s signals=%s unresolved=%s",
        len(initial_events),
        len(state.signals),
        len(state.unresolved_actions),
    )

    position = (
        settings.telegram_event_log_path.stat().st_size
        if settings.telegram_event_log_path.exists()
        else 0
    )

    while True:
        path = settings.telegram_event_log_path
        if not path.exists():
            await asyncio.sleep(1)
            continue

        current_size = path.stat().st_size
        if current_size < position:
            logger.warning("Telegram event log was truncated; rebuilding state")
            state = SignalState()
            for event in _read_jsonl(path):
                state.ingest_event(event, rebuild=False)
            state.rebuild()
            _write_snapshot(settings.signal_state_path, state)
            position = current_size
            await asyncio.sleep(1)
            continue

        if current_size > position:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(position)
                while True:
                    line = handle.readline()
                    if not line:
                        break
                    position = handle.tell()
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed live JSONL event")
                        continue
                    state.ingest_event(event)
                    _write_snapshot(settings.signal_state_path, state)

            logger.info(
                "Signal state updated signals=%s unresolved=%s",
                len(state.signals),
                len(state.unresolved_actions),
            )

        await asyncio.sleep(1)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
