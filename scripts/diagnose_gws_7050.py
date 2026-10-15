#!/usr/bin/env python3
"""Read-only trace of GWS signal 7050 and later stop-loss corrections."""
import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

CHAT = -1003765237177
ROOT = 7050


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    path = args.data_dir / "telegram-events.jsonl"
    print("Telegram event log:", path)
    count = 0
    try:
        with path.open(encoding="utf-8") as events:
            for line in events:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if str(item.get("chat_id")) != str(CHAT):
                    continue
                if item.get("message_id") not in (7050, 7051):
                    continue
                print(
                    "Event", item.get("event_type"), "message", item.get("message_id"),
                    "date", item.get("date"), "edit_date", item.get("edit_date"),
                )
                stop_lines = [
                    text.strip() for text in str(item.get("text") or "").splitlines()
                    if text.strip().upper().startswith("SL")
                ]
                print("Stop lines:", stop_lines)
                count += 1
    except OSError as exc:
        print("Unable to read events:", type(exc).__name__)
    print("Matching event versions:", count)

    snapshot = load_json(args.data_dir / "signal-state.json")
    entry = next(
        (s for s in snapshot.get("signals", [])
         if isinstance(s, dict) and s.get("signal_id") == f"{CHAT}:{ROOT}"),
        None,
    )
    if entry is None:
        print("Signal not present in snapshot")
    else:
        for field in (
            "signal_id", "opened_at", "last_update_at", "entry_low",
            "entry_high", "original_sl", "current_sl", "status",
            "source_message_ids",
        ):
            print("State", field, "=", entry.get(field))
        try:
            opened = datetime.fromisoformat(entry["opened_at"].replace("Z", "+00:00"))
            print("Signal age seconds:", round((datetime.now(UTC) - opened).total_seconds()))
        except (ValueError, TypeError, KeyError):
            pass

    telemetry = load_json(args.data_dir / "mt5-account1.json")
    print("MT5 telemetry:", {
        k: telemetry.get(k)
        for k in ("received_at", "bid", "ask", "feed_http", "entry_enabled")
    })


if __name__ == "__main__":
    main()
