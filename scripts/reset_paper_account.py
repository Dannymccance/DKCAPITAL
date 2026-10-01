from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dkcapital.config import Settings
from dkcapital.paper_trading import PaperAccount


DELAY_STATE_VERSION = 1


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


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
    backup = path.with_name("{}.pre-reset-{}".format(path.name, stamp))
    shutil.copy2(path, backup)
    return backup


def run() -> None:
    settings = Settings.from_env()
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")

    backups = [
        backup
        for path in (
            settings.paper_state_path,
            settings.paper_event_log_path,
            settings.paper_delay_state_path,
        )
        if (backup := _backup(path, stamp)) is not None
    ]

    account = PaperAccount(
        starting_balance_usd=settings.paper_starting_balance_usd,
        symbol=settings.paper_symbol,
        strategy_mode=settings.paper_strategy_mode,
        entry_policy=settings.paper_entry_policy,
    )
    account.activate_strategy(
        settings.paper_strategy_mode,
        activated_at=now.isoformat(),
    )

    _write_json(settings.paper_state_path, account.snapshot())

    settings.paper_event_log_path.parent.mkdir(parents=True, exist_ok=True)
    settings.paper_event_log_path.write_text("", encoding="utf-8")

    telegram_offset = _file_size(settings.telegram_event_log_path)
    market_offset = _file_size(settings.paper_market_tape_path)
    delay_state = {
        "version": DELAY_STATE_VERSION,
        "activated_at": now.isoformat(),
        "baseline_telegram_offset": telegram_offset,
        "telegram_offset": telegram_offset,
        "market_offset": market_offset,
        "last_telegram_processed_at": None,
        "last_market_processed_at": None,
    }
    _write_json(settings.paper_delay_state_path, delay_state)

    print("DK Capital paper account reset complete.")
    print("Starting balance: ${:,.2f}".format(settings.paper_starting_balance_usd))
    print("Strategy mode: {}".format(settings.paper_strategy_mode))
    print("Entry policy: {}".format(settings.paper_entry_policy))
    print("Simulation delay: {} seconds".format(settings.paper_simulation_delay_seconds))
    print("Telegram baseline offset: {}".format(telegram_offset))
    print("Market tape baseline offset: {}".format(market_offset))
    print("Existing Telegram history and market tape were preserved.")
    if backups:
        print("Backups:")
        for path in backups:
            print("  {}".format(path))


if __name__ == "__main__":
    run()
